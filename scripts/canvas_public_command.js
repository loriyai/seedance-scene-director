#!/usr/bin/env node
"use strict";

// Execute only the official public command catalogue. Preconditions are checked
// against the same live store that its handler changes, before that handler runs.
const fs = require("fs");
const path = require("path");
const { isDeepStrictEqual } = require("util");

function normalizeAsset(asset) {
  if (asset === null) return null;
  const result = structuredClone(asset);
  const content = result?.content;
  const data = content?.data && typeof content.data === "object" ? content.data : content;
  if (data?.basicInfo && [undefined, null, ""].includes(data.basicInfo.appearanceName)) {
    delete data.basicInfo.appearanceName;
  }
  if (Array.isArray(data?.generation?.references) && data.generation.references.length === 0) {
    delete data.generation.references;
  }
  // The live SDK adds this service revision to an envelope omitted by the
  // native snapshot adapter. It is transport metadata, not business content.
  if (result?.assetExtra && typeof result.assetExtra === "object" && !Array.isArray(result.assetExtra)) {
    delete result.assetExtra.asset_content_version;
    if (Object.keys(result.assetExtra).length === 0) delete result.assetExtra;
  }
  return result;
}

function reject(code, message, nodeId) {
  const error = new Error(message);
  error.code = code;
  error.notSent = true;
  error.nodeId = nodeId;
  throw error;
}

function conflict(message, nodeId) {
  reject("CONCURRENT_EDIT", message, nodeId);
}

function unknown(message, nodeId) {
  reject("READBACK_PENDING", message, nodeId);
}

function sourceNode(node) {
  if (node === null || typeof node !== "object") return node;
  return Object.fromEntries(["type", "data"].filter(key => Object.hasOwn(node, key)).map(key => [key, node[key]]));
}

// Absence in a returned snapshot is not evidence that a user cleared a field.
// Explicit null/empty values still take part in the ordinary value comparison.
function compareObserved(actual, expected) {
  if (isDeepStrictEqual(actual, expected)) return "matched";
  if (Array.isArray(actual) && Array.isArray(expected)) {
    if (actual.length !== expected.length) return "conflict";
    let missing = false;
    for (let index = 0; index < expected.length; index += 1) {
      const state = compareObserved(actual[index], expected[index]);
      if (state === "conflict") return state;
      if (state === "unknown") missing = true;
    }
    return missing ? "unknown" : "matched";
  }
  if (actual && expected && typeof actual === "object" && typeof expected === "object" &&
      !Array.isArray(actual) && !Array.isArray(expected)) {
    let missing = false;
    for (const key of Object.keys(expected)) {
      if (!Object.hasOwn(actual, key)) { missing = true; continue; }
      const state = compareObserved(actual[key], expected[key]);
      if (state === "conflict") return state;
      if (state === "unknown") missing = true;
    }
    if (Object.keys(actual).some(key => !Object.hasOwn(expected, key))) return "conflict";
    return missing ? "unknown" : "matched";
  }
  return "conflict";
}

function assertExpected(document, canvasId, expectedNodes, expectedEdges = []) {
  const root = document?.assets?.[canvasId];
  if (document?.rootCanvasId && document.rootCanvasId !== canvasId) {
    conflict("执行前画布身份不同，命令尚未执行。");
  }
  if (!document?.rootCanvasId || !root?.content?.nodes || !root.content.edges) {
    unknown("执行前画布根图不完整，待重新读取；命令尚未执行。");
  }
  if (!Array.isArray(expectedNodes) || expectedNodes.length === 0) {
    conflict("带前置核验的写入必须提供预期节点。");
  }
  const seen = new Set();
  for (const expected of expectedNodes) {
    const id = expected.node_id;
    if (typeof id !== "string" || seen.has(id) || !Object.hasOwn(expected, "node") || !Object.hasOwn(expected, "asset")) {
      conflict("写入前置核验记录不完整或重复。");
    }
    seen.add(id);
    if ((expected.node !== null && !Object.hasOwn(root.content.nodes, id)) ||
        (expected.asset !== null && !Object.hasOwn(document.assets, id))) {
      unknown("目标或引用资产未完整返回，待重新读取；命令尚未执行。", id);
    }
    const node = Object.hasOwn(root.content.nodes, id) ? root.content.nodes[id] : null;
    const asset = Object.hasOwn(document.assets, id) ? document.assets[id] : null;
    const nodeState = compareObserved(expected.source_only ? sourceNode(node) : node,
      expected.source_only ? sourceNode(expected.node) : expected.node);
    const assetState = compareObserved(normalizeAsset(asset), normalizeAsset(expected.asset));
    if (nodeState === "conflict" || assetState === "conflict") {
      conflict("目标或引用资产在编译后发生变化，命令尚未执行。", id);
    }
    if (nodeState === "unknown" || assetState === "unknown") {
      unknown("目标或引用资产字段未完整返回，待重新读取；命令尚未执行。", id);
    }
  }
  for (const expected of expectedEdges) {
    if (typeof expected.edge_id !== "string" || !Object.hasOwn(expected, "value")) {
      conflict("引用边前置核验记录不完整，命令尚未执行。");
    }
    if (expected.value !== null && !Object.hasOwn(root.content.edges, expected.edge_id)) {
      unknown("引用边未完整返回，待重新读取；命令尚未执行。");
    }
    const value = Object.hasOwn(root.content.edges, expected.edge_id) ? root.content.edges[expected.edge_id] : null;
    const state = compareObserved(value, expected.value);
    if (state === "conflict") {
      conflict("引用边在编译后发生变化，命令尚未执行。");
    }
    if (state === "unknown") unknown("引用边字段未完整返回，待重新读取；命令尚未执行。");
  }
}

function guardedSdk(sdk, input, observation) {
  return {
    ...sdk,
    createPippitAssetSdkCanvasLoader(options) {
      // Snapshot initialization must not save automatic layout repairs before
      // the guarded public operation has been admitted.
      return sdk.createPippitAssetSdkCanvasLoader({ ...options, readOnly: true, isLayoutActive: () => false });
    },
    createXyqCanvasOpencodeToolDefinitions(options) {
      const definitions = sdk.createXyqCanvasOpencodeToolDefinitions(options);
      if (typeof options.runtime?.store?.getState !== "function") return definitions;
      const execute = definitions.apply_mutations.execute;
      return {
        ...definitions,
        apply_mutations: {
          ...definitions.apply_mutations,
          execute(value) {
            let revision;
            try {
              assertExpected(options.runtime.store.getState(), input.canvas_id, input.expected_nodes, input.expected_edges);
              revision = options.runtime.store.getRevision?.();
              if (!Number.isFinite(revision)) unknown("执行前画布版本未完整返回，命令尚未执行。");
            } catch (error) {
              observation.guardError = error;
              throw error;
            }
            observation.checked = true;
            // The official handler awaits permission and media preparation.
            // Its own final revision check must use this same store revision,
            // never the unrelated revision from the earlier compiler process.
            return execute.call(definitions.apply_mutations, { ...value, expectedRevision: revision });
          },
        },
      };
    },
  };
}

async function main(input, dependencies = {}) {
  if (!["apply_mutations", "xyq.generation.update_prompt", "xyq.role.updateDescription",
        "xyq.scene.updateDescription", "xyq.role.updateAppearance"].includes(input.name)) {
    throw new Error("该前置核验适配器不支持此公开命令。");
  }
  const packageRoot = path.resolve(path.dirname(input.canvas_entry), "..");
  const pkg = JSON.parse(fs.readFileSync(path.join(packageRoot, "package.json"), "utf8"));
  if (pkg.version !== "1.0.57") throw new Error("该写入适配器仅支持 CLI 1.0.57。");
  const helper = dependencies.helper || require(path.join(packageRoot, "scripts", "canvas-command.js"));
  const sdk = dependencies.sdk || require(path.join(packageRoot, "dist", "xyq-canvas-command-runtime.cjs"));
  helper.ensureNodeRuntimeGlobals();
  const delay = dependencies.delay || (milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds)));
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const observation = { checked: false, guardError: null };
    let output = "";
    try {
      const code = await helper.runCanvasCommand([
        "canvas", "command", "run", input.name, "--canvas-id", input.canvas_id,
        "--team-id", input.team_id, "--input", JSON.stringify(input.input),
      ], {
        sdk: guardedSdk(sdk, input, observation), cwd: input.project,
        nativeInvocation: { command: input.cli_path,
          env: { ...process.env, PIPPIT_CLI_DISABLE_UPDATE_CHECK: "1" } },
        stdout: { write(value) { output += value; } }, stderr: process.stderr,
      });
      if (!observation.checked) throw new Error("官方命令未经过同一会话前置核验。");
      const result = JSON.parse(output);
      return { result, code };
    } catch (error) {
      if (observation.guardError) {
        // Only a failed pre-handler read may open a fresh runtime. Once the
        // official handler was entered, any uncertain outcome returns upward
        // for read-only reconciliation and is never replayed here.
        if (!observation.checked && observation.guardError.code === "READBACK_PENDING" && attempt < 2) {
          await delay(1000 * (attempt + 1));
          continue;
        }
        return { code: 1, result: { ok: false, code: observation.guardError.code, not_sent: true,
          message: observation.guardError.message, attempts: attempt + 1,
          ...(observation.guardError.nodeId ? { node_id: observation.guardError.nodeId } : {}) } };
      }
      throw error;
    }
  }
}

module.exports = { assertExpected, normalizeAsset, compareObserved, guardedSdk, main };
if (require.main === module) {
  Promise.resolve().then(() => {
    if (process.argv.length !== 3) throw new Error("Usage: node canvas_public_command.js INPUT.json");
    return main(JSON.parse(fs.readFileSync(process.argv[2], "utf8")));
  }).then(({ result, code }) => {
    process.stdout.write(JSON.stringify(result) + "\n");
    process.exitCode = code;
  }).catch(error => {
    process.stderr.write(String(error.message || error) + "\n");
    process.exitCode = 1;
  });
}

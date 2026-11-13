#!/usr/bin/env node
"use strict";

// Execute only the official public command catalogue. Preconditions are checked
// against the same live store that its handler changes, before that handler runs.
const fs = require("fs");
const path = require("path");
const { createHash, randomUUID } = require("crypto");
const { isDeepStrictEqual } = require("util");

const READ_ATTEMPTS = 3;
const READ_BUDGET_MS = 60000;

function retryableRead(error) {
  const message = String(error?.message || error);
  if (/\b(?:401|403)\b|unauthori[sz]ed|forbidden|not logged in|登录态|授权/i.test(message)) return false;
  return error?.code === "READBACK_PENDING" || /\bEOF\b|ECONNRESET|ECONNREFUSED|ETIMEDOUT|EAI_AGAIN|ENETUNREACH|socket hang up|connection (?:reset|closed|aborted)|forcibly closed|wsarecv|TLS handshake|timed? ?out|network error|fetch failed|\b(?:429|502|503|504)\b/i.test(message);
}

function readRecovery(dependencies = {}) {
  const now = dependencies.now || Date.now;
  return { now, deadline: now() + READ_BUDGET_MS,
    delay: dependencies.delay || (milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds))) };
}

async function retryRead(action, recovery, signal) {
  for (let attempt = 0; ; attempt += 1) {
    if (signal?.aborted) throw signal.reason || new Error("Read aborted");
    try { return await action(); }
    catch (error) {
      const wait = 1000 * (attempt + 1);
      if (signal?.aborted || !retryableRead(error) || attempt + 1 >= READ_ATTEMPTS ||
          recovery.now() + wait >= recovery.deadline) throw error;
      await recovery.delay(wait);
    }
  }
}

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

function sourceIdentityState(node, asset, expected) {
  const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
  if (!object(expected.node) || !object(expected.asset) ||
      typeof expected.node.type !== "string" || typeof expected.asset.type !== "string") return "unknown";
  if (node === undefined || asset === undefined) return "unknown";
  if (!object(node) || !object(asset)) return "conflict";
  if (!Object.hasOwn(node, "type") || !Object.hasOwn(asset, "type") || !Object.hasOwn(asset, "content") ||
      (Object.hasOwn(expected.asset, "pippitAssetId") && !Object.hasOwn(asset, "pippitAssetId"))) return "unknown";
  if (node.type !== expected.node.type || asset.type !== expected.asset.type ||
      (Object.hasOwn(asset, "pippitAssetId") && asset.pippitAssetId !== expected.node_id) || !object(asset.content) ||
      (Object.hasOwn(node, "id") && node.id !== expected.node_id)) return "conflict";
  // Sources are bound by their stable node identity. User edits to names,
  // prompts, pictures, media fields and layout are valid current source data.
  return "matched";
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

function generationData(asset) {
  const content = asset?.content;
  const data = content?.data && typeof content.data === "object" && !Array.isArray(content.data) ? content.data : content;
  return data?.generation;
}

function compareReferenceIdentities(actual, expected, document) {
  if (!Array.isArray(actual) || !Array.isArray(expected)) return compareObserved(actual, expected);
  if (actual.length !== expected.length) return "conflict";
  const nodes = document?.assets?.[document.rootCanvasId]?.content?.nodes || {};
  const identity = row => {
    if (!row || typeof row !== "object" || Array.isArray(row)) return row;
    const source = row.sourceNodeId ?? row.nodeAssetId ?? (Object.hasOwn(nodes, row.id) ? row.id : undefined);
    return { ...(source === undefined ? {} : { sourceNodeId: source }),
      ...(Object.hasOwn(row, "type") ? { type: row.type } : {}) };
  };
  const remaining = actual.map(identity);
  let unknown = false;
  for (const row of expected.map(identity)) {
    const states = remaining.map(item => compareObserved(item, row));
    let index = states.indexOf("matched");
    if (index < 0) { index = states.indexOf("unknown"); unknown = true; }
    if (index < 0) return "conflict";
    remaining.splice(index, 1);
  }
  return unknown ? "unknown" : "matched";
}

function targetAssetState(actual, expected, document, referenceIdentityOnly) {
  const normalized = normalizeAsset(actual), planned = normalizeAsset(expected);
  const actualGeneration = generationData(actual), expectedGeneration = generationData(expected);
  if (!referenceIdentityOnly || !actualGeneration || !expectedGeneration ||
      !Object.hasOwn(actualGeneration, "references") || !Object.hasOwn(expectedGeneration, "references")) {
    return compareObserved(normalized, planned);
  }
  const references = compareReferenceIdentities(actualGeneration.references, expectedGeneration.references, document);
  delete generationData(normalized).references;
  delete generationData(planned).references;
  const rest = compareObserved(normalized, planned);
  if (references === "conflict" || rest === "conflict") return "conflict";
  return references === "unknown" || rest === "unknown" ? "unknown" : "matched";
}

function allowsReferenceIdentity(input) {
  // This public command resolves current source material in the live store.
  // An ordinary data/asset update may write a compiled old references array,
  // so its guard must remain exact even if a caller supplies the marker.
  return input.name === "xyq.generation.update_prompt";
}

function assertExpected(document, canvasId, expectedNodes, expectedEdges = [], expectedEdgeTargets = [], allowReferenceIdentity = false) {
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
    if (expected.identity_only) {
      const identity = sourceIdentityState(node, asset, expected);
      if (identity === "unknown") unknown("引用来源身份或资产未完整返回，待重新读取；命令尚未执行。", id);
      if (identity === "conflict") conflict("引用来源节点身份或类型发生变化，命令尚未执行。", id);
      continue;
    }
    const nodeState = compareObserved(expected.source_only ? sourceNode(node) : node,
      expected.source_only ? sourceNode(expected.node) : expected.node);
    const assetState = targetAssetState(asset, expected.asset, document,
      allowReferenceIdentity && expected.reference_identity_only === true && expected.node?.type === "biz/video");
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
  for (const target of expectedEdgeTargets) {
    const expectedIds = new Set(expectedEdges.filter(item => item.value?.target === target).map(item => item.edge_id));
    for (const [id, edge] of Object.entries(root.content.edges)) {
      if (edge?.target === target && !expectedIds.has(id)) {
        conflict("目标出现了编译时未包含的引用边，命令尚未执行。", target);
      }
    }
  }
}

function requiredAssetRequests(snapshot, input) {
  const document = snapshot?.state;
  const nodes = document?.assets?.[input.canvas_id]?.content?.nodes;
  if (!nodes || !document?.assets) return [];
  return (input.expected_nodes || []).filter(expected => {
    if (!expected.asset || !Object.hasOwn(nodes, expected.node_id)) return false;
    const actual = document.assets[expected.node_id];
    const comparison = expected.identity_only
      ? sourceIdentityState(nodes[expected.node_id], actual, expected)
      : targetAssetState(actual, expected.asset, document,
        allowsReferenceIdentity(input) && expected.reference_identity_only === true && expected.node?.type === "biz/video");
    return !Object.hasOwn(document.assets, expected.node_id) ||
      comparison === "unknown" ||
      !Number.isSafeInteger(snapshot.assetVersions?.[expected.node_id]) || snapshot.assetVersions[expected.node_id] < 0;
  }).map(expected => ({ assetId: expected.node_id, assetKind: "node", nodeType: nodes[expected.node_id]?.type }));
}

async function hydrateRequiredAssets(loader, snapshot, request, input, recovery) {
  if (!Number.isSafeInteger(snapshot?.assetVersions?.[input.canvas_id]) || snapshot.assetVersions[input.canvas_id] < 0) {
    unknown("画布根版本未完整返回，待重新读取。");
  }
  const requests = requiredAssetRequests(snapshot, input);
  if (!requests.length) return snapshot;
  if (typeof loader.fetchAssets !== "function") unknown("官方读取器没有返回完整目标资产，待重新读取。");
  return retryRead(async () => {
    const fetched = await loader.fetchAssets({ scopeAssetId: request.scopeAssetId, requests, signal: request.signal });
    const assets = { ...snapshot.state.assets };
    const assetVersions = { ...snapshot.assetVersions };
    const rows = new Map((fetched?.assets || []).map(row => [row.assetId, row]));
    for (const { assetId } of requests) {
      const row = rows.get(assetId);
      const version = fetched?.assetVersions?.[assetId] ?? row?.assetVersion;
      // Return the official content and its own version together. Never put a
      // planned value into the live store, or let a missing reply erase it.
      if (!row?.asset || !Number.isSafeInteger(version) || version < 0) {
        unknown("目标资产或版本未完整返回，待定向重新读取。", assetId);
      }
      assets[assetId] = row.asset;
      assetVersions[assetId] = version;
    }
    const result = { ...snapshot, state: { ...snapshot.state, assets }, assetVersions };
    if (requiredAssetRequests(result, input).length) unknown("目标资产字段未完整返回，待定向重新读取。");
    return result;
  }, recovery, request.signal);
}

function guardedPersistence(persistence, observation, recovery) {
  if (typeof persistence?.loadOutbound !== "function") return persistence;
  return { ...persistence, async loadOutbound() {
    const envelopes = await persistence.loadOutbound();
    if (!Array.isArray(envelopes) || envelopes.length === 0) return envelopes;
    const error = new Error("发现本操作此前未确认事务；已阻止启动时重放，先按原节点回读恢复。");
    error.code = "READBACK_PENDING";
    error.notSent = false;
    try {
      // Only this explicit operation's isolated directory belongs to this
      // recovery. Unknown/global writers are never cleared or quarantined.
      if (recovery.knownOperation && persistence.statePath &&
          path.dirname(path.resolve(persistence.statePath)) === recovery.stateDirectory &&
          typeof persistence.quarantine === "function") {
        error.recoveryPath = await persistence.quarantine();
      }
    } catch (cause) {
      error.message = "旧事务隔离暂未完成，已阻止自动重放；稍后继续回读恢复。";
      error.cause = cause;
    }
    observation.bootstrapError = error;
    // Do not hand old envelopes to the SDK: rejecting a rebase later can make
    // its outbox discard those envelopes before the caller records evidence.
    throw error;
  } };
}

function guardedSdk(sdk, input, observation, recovery = readRecovery()) {
  return {
    ...sdk,
    createPippitAssetServiceTransport(options) {
      return sdk.createPippitAssetServiceTransport({ ...options,
        // This hook wraps only the official read RPC. Mutation/ID allocation
        // callbacks retain the official one-shot behavior.
        async assetQuery(...args) {
          try { return await retryRead(() => options.assetQuery(...args), recovery, args[1]?.signal); }
          catch (error) {
            if (!retryableRead(error)) observation.fatalReadError = error;
            throw error;
          }
        },
      });
    },
    createPippitAssetSdkCanvasLoader(options) {
      // Snapshot initialization must not save automatic layout repairs before
      // the guarded public operation has been admitted.
      const priorityIds = (input.expected_nodes || []).filter(item => item.asset !== null).map(item => item.node_id);
      const loader = sdk.createPippitAssetSdkCanvasLoader({ ...options, readOnly: true, isLayoutActive: () => false,
        resolvePriorityNodeAssetIds: document => [...new Set([...priorityIds,
          ...(options.resolvePriorityNodeAssetIds?.(document) || [])])],
      });
      return { ...loader, async fetchSnapshot(request) {
        const snapshot = await loader.fetchSnapshot(request);
        // The SDK can settle a child-query failure as a missing asset. An auth
        // or validation failure must retain its original non-retryable cause.
        if (observation.fatalReadError) throw observation.fatalReadError;
        // Returning the combined snapshot here lets the official transport
        // remember every fetched asset version before attaching its store.
        return hydrateRequiredAssets(loader, snapshot, request, input, recovery);
      } };
    },
    createXyqCanvasCommandRuntime(options) {
      const { createRebaseGuard } = require("./canvas_rebase_guard");
      const guard = createRebaseGuard({ input, observation,
        assertExpected: (...args) => assertExpected(...args, allowsReferenceIdentity(input)) });
      return sdk.createXyqCanvasCommandRuntime({ ...options,
        persistence: guardedPersistence(options.persistence, observation, recovery), sync: { ...options.sync,
        validateRebasedEnvelope(context) {
          guard(context);
          return options.sync?.validateRebasedEnvelope?.(context);
        },
      } });
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
            if (observation.bootstrapError || observation.rebaseError) throw observation.bootstrapError || observation.rebaseError;
            let revision;
            try {
              assertExpected(options.runtime.store.getState(), input.canvas_id, input.expected_nodes, input.expected_edges,
                input.expected_edge_targets, allowsReferenceIdentity(input));
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
  const recovery = readRecovery(dependencies);
  recovery.knownOperation = typeof input.operation_id === "string" && input.operation_id.trim().length > 0;
  const operationId = recovery.knownOperation ? input.operation_id : randomUUID();
  const namespace = createHash("sha256").update(JSON.stringify([input.canvas_id, operationId])).digest("hex");
  recovery.stateDirectory = path.resolve(input.project, "canvas", "cli-state", namespace);
  for (let attempt = 0; attempt < READ_ATTEMPTS; attempt += 1) {
    const observation = { checked: false, guardError: null };
    let output = "";
    try {
      const code = await helper.runCanvasCommand([
        "canvas", "command", "run", input.name, "--canvas-id", input.canvas_id,
        "--team-id", input.team_id, "--input", JSON.stringify(input.input),
      ], {
        sdk: guardedSdk(sdk, input, observation, recovery), cwd: input.project,
        stateDirectory: recovery.stateDirectory,
        nativeInvocation: { command: input.cli_path,
          env: { ...process.env, PIPPIT_CLI_DISABLE_UPDATE_CHECK: "1" } },
        stdout: { write(value) { output += value; } }, stderr: process.stderr,
      });
      if (observation.bootstrapError || observation.rebaseError) throw observation.bootstrapError || observation.rebaseError;
      if (!observation.checked) throw new Error("官方命令未经过同一会话前置核验。");
      const result = JSON.parse(output);
      return { result, code };
    } catch (error) {
      if (observation.bootstrapError || observation.rebaseError) {
        const pending = observation.bootstrapError || observation.rebaseError;
        return { code: 1, result: { ok: false, code: pending.code || "CONCURRENT_EDIT",
          not_sent: false, message: pending.message, attempts: attempt + 1,
          ...(pending.recoveryPath ? { recovery_path: pending.recoveryPath } : {}) } };
      }
      const readError = observation.guardError || error;
      if (!observation.checked && (observation.guardError || retryableRead(readError))) {
        // Only a failed pre-handler read may open a fresh runtime. Once the
        // official handler was entered, any uncertain outcome returns upward
        // for read-only reconciliation and is never replayed here.
        if (retryableRead(readError) && attempt + 1 < READ_ATTEMPTS &&
            recovery.now() + 1000 * (attempt + 1) < recovery.deadline) {
          await recovery.delay(1000 * (attempt + 1));
          continue;
        }
        return { code: 1, result: { ok: false, code: readError.code === "CONCURRENT_EDIT" ? "CONCURRENT_EDIT" : "READBACK_PENDING",
          not_sent: true, message: readError.message, attempts: attempt + 1,
          ...(readError.nodeId ? { node_id: readError.nodeId } : {}) } };
      }
      throw error;
    }
  }
}

module.exports = { assertExpected, normalizeAsset, compareObserved, guardedSdk, main,
  requiredAssetRequests, hydrateRequiredAssets, retryableRead, retryRead, readRecovery, guardedPersistence, sourceIdentityState,
  compareReferenceIdentities, targetAssetState };
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

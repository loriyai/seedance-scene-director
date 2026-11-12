#!/usr/bin/env node
"use strict";

// Adapter to the installed official SDK. Never rewrites or monkey-patches the CLI.
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { isDeepStrictEqual } = require("util");
const { Worker, isMainThread, parentPort, workerData } = require("worker_threads");

const PROTOCOL = "sdk-local-batch-v1";
const MUTATIONS = new Set(["create_biz_node", "update_node_data", "update_asset", "create_edge", "delete_edges"]);
const COMMANDS = new Set(["xyq.generation.update_prompt", "xyq.role.updateDescription", "xyq.scene.updateDescription", "xyq.role.updateAppearance"]);
const PERMISSIONS = ["canvas.read", "canvas.write", "canvas.patch", "canvas.asset.read", "canvas.asset.write"];
const clone = value => JSON.parse(JSON.stringify(value));

function official(entry) {
  const packageRoot = path.resolve(path.dirname(fs.realpathSync(entry)), "..");
  const runtimePath = path.join(packageRoot, "dist", "xyq-canvas-command-runtime.cjs");
  const helper = require(path.join(packageRoot, "scripts", "canvas-command.js"));
  const sdk = require(runtimePath);
  for (const name of ["createXyqCanvasCommandRuntime", "createXyqCanvasOpencodeToolDefinitions"]) {
    if (typeof sdk[name] !== "function") throw new Error(`官方 SDK 缺少 ${name}`);
  }
  helper.ensureNodeRuntimeGlobals();
  return { sdk, helper, runtimeSha256: crypto.createHash("sha256").update(fs.readFileSync(runtimePath)).digest("hex") };
}

function localRuntime(sdk, helper, document) {
  const runtime = sdk.createXyqCanvasCommandRuntime({
    canvasId: document.rootCanvasId,
    persistence: helper.createMemoryPersistence(),
    transportFactory() { throw new Error("本地编译禁止启动网络同步"); },
  });
  // Deliberately do not call canvas.start/whenReady/flush. All business operations
  // execute against an isolated official store, with no transport or local outbox.
  runtime.store.applyRemoteSnapshot(clone(document));
  return runtime;
}

async function compile(input, loaded) {
  const { sdk, helper, runtimeSha256 } = loaded;
  if (!input.document?.assets?.[input.document.rootCanvasId] || !Array.isArray(input.commands)) {
    throw new Error("批量编译需要完整画布快照和命令清单");
  }
  const runtime = localRuntime(sdk, helper, input.document);
  const differ = localRuntime(sdk, helper, input.document);
  try {
    const definitions = sdk.createXyqCanvasOpencodeToolDefinitions({
      runtime: { store: runtime.store, permissions: PERMISSIONS },
      schema: helper.createSchemaFactory(),
      allocateNodeId: async () => { throw new Error("节点 ID 必须预先申请并保存"); },
      queryMediaAsset: async () => { throw new Error("本地编译缺少媒体身份快照，禁止临时联网补查"); },
    });
    for (const command of input.commands) {
      const { name, input: value } = command;
      if (!MUTATIONS.has(name) && !COMMANDS.has(name)) throw new Error(`不支持的批量命令：${name}`);
      // Registered business commands still run alone, through their unchanged
      // official handler. Only the resulting document delta is combined later.
      const mutation = MUTATIONS.has(name) ? { ...value, kind: name }
        : { kind: "invoke_command", name, args: [value] };
      const raw = await definitions.apply_mutations.execute({ atomic: true, intent: `seedance compile ${name}`, mutations: [mutation] });
      const result = typeof raw === "string" ? JSON.parse(raw) : raw;
      if (result.ok !== true) throw new Error(JSON.stringify(result));
    }
    const document = clone(runtime.store.getState());
    const delta = differ.store.applyRemoteSnapshot(document);
    return { protocol: PROTOCOL, document, patches: delta.transaction?.forwardPatches || [], runtime_sha256: runtimeSha256 };
  } finally {
    runtime.canvas.dispose();
    differ.canvas.dispose();
  }
}

async function snapshot(input, loaded) {
  const { sdk, helper, runtimeSha256 } = loaded;
  if (!input.canvas_id || typeof input.team_id !== "string") throw new Error("批量快照需要明确画布和空间");
  const nativeClient = new helper.NativeCanvasClient({ command: input.cli_path }, { cwd: input.project, teamID: input.team_id });
  const { assetRuntime, loader } = helper.createCanvasAssetRuntime({ nativeClient, sdk });
  try {
    const result = await loader.fetchSnapshot({ scopeAssetId: input.canvas_id });
    if (result.state?.rootCanvasId !== input.canvas_id) throw new Error("批量快照画布身份不符");
    return { protocol: PROTOCOL, document: result.state, asset_versions: result.assetVersions, runtime_sha256: runtimeSha256 };
  } finally { assetRuntime.dispose(); }
}

function semanticGroups(commands) {
  const groups = [];
  for (const item of commands) {
    if (!item || (!MUTATIONS.has(item.name) && !COMMANDS.has(item.name)) ||
        !item.input || typeof item.input !== "object" || Array.isArray(item.input)) {
      throw new Error("画布业务命令或输入无效");
    }
    // The public CLI requires registered commands to execute independently.
    const last = groups[groups.length - 1];
    if (MUTATIONS.has(item.name) && last && MUTATIONS.has(last[0].name)) last.push(item);
    else groups.push([item]);
  }
  return groups;
}

function refreshVideoSettings(group, document) {
  return group.map(item => {
    if (item.generation_overrides === undefined) return item;
    const changes = item.generation_overrides;
    if (item.name !== "update_node_data" || !changes || typeof changes !== "object" ||
        Array.isArray(changes) || !Object.keys(changes).length ||
        Object.entries(changes).some(([k, v]) => !["durationMs", "ratio"].includes(k) ||
          v !== item.input.patch?.generation?.[k])) {
      throw new Error("分镜生成参数变更与预检不一致");
    }
    const content = document.assets[item.input.id]?.content;
    if (!content) throw new Error("待更新的分镜节点缺失");
    const generation = (content.data || content).generation || {};
    return { ...item, input: { ...item.input, patch: { ...item.input.patch,
      generation: { ...generation, ...changes } } } };
  });
}

async function publicWrite(input, loaded, group) {
  const structural = MUTATIONS.has(group[0].name);
  const name = structural ? "apply_mutations" : group[0].name;
  const value = structural ? {
    atomic: true, intent: "seedance canvas sync",
    mutations: group.map(item => ({ ...item.input, kind: item.name })),
  } : group[0].input;
  // This is the same public command runner as `canvas command run`. Let it own
  // authentication, command preparation, SDK persistence, ACKs and error recovery.
  let output = "";
  const exitCode = await loaded.helper.runCanvasCommand(
    ["canvas", "command", "run", name, "--canvas-id", input.canvas_id,
      "--team-id", input.team_id, "--input", JSON.stringify(value)],
    { cwd: input.project, nativeInvocation: { command: input.cli_path }, sdk: loaded.sdk,
      stdout: { write(chunk) { output += chunk; } } });
  const result = JSON.parse(output);
  if (result.ok === false) return { rejected: true, message: result.message || result.code };
  const rows = result.data?.results;
  if (exitCode !== 0 || result.ok !== true || !Array.isArray(rows) || !rows.length ||
      rows.some(row => !["committed", "noop"].includes(row.status))) {
    throw new Error("公开画布命令未确认全部操作");
  }
  return { rejected: false };
}

async function semanticWrite(input, loaded) {
  let completed = 0;
  let awaitingResult = false;
  const result = (status, message) => ({ protocol: PROTOCOL, status, completed, message });
  try {
    if (!input.canvas_id || typeof input.team_id !== "string" || !Array.isArray(input.commands) ||
        !input.commands.length || !Number.isSafeInteger(input.expected_versions?.[input.canvas_id])) {
      return result("rejected", "语义写入缺少画布、空间、命令或根资产版本");
    }
    const groups = semanticGroups(input.commands);
    const guarded = Object.entries(input.expected_versions);
    let fresh = await snapshot(input, loaded);
    if (guarded.some(([id, version]) => !Number.isSafeInteger(version) || version < 0 ||
        (fresh.document.assets[id] ? fresh.asset_versions[id] : 0) !== version)) {
      return result("conflict", "提交前画布资产版本已变化");
    }
    for (let index = 0; index < groups.length; index++) {
      const group = refreshVideoSettings(groups[index], fresh.document);
      if (fresh.document.assets[input.canvas_id].content.settings?.readOnly) {
        return result(completed ? "uncertain" : "rejected", "目标画布已变为只读");
      }
      // Calculate the expected intermediate picture through the official local
      // SDK. These patches are never sent to the public writer.
      const expected = index + 1 < groups.length
        ? (await compile({ document: fresh.document, commands: group }, loaded)).document : null;
      awaitingResult = true;
      const response = await publicWrite(input, loaded, group);
      awaitingResult = false;
      if (response.rejected) return result(completed ? "uncertain" : "rejected", response.message);
      completed += group.length;
      if (expected) {
        fresh = await snapshot(input, loaded);
        if (guarded.some(([id]) =>
          !isDeepStrictEqual(expected.assets[id]?.content, fresh.document.assets[id]?.content))) {
          return result("uncertain", "已确认步骤之后画布内容与预期不一致，停止后续写入");
        }
      }
    }
    return result("acknowledged");
  } catch (error) {
    return result(completed || awaitingResult ? "uncertain" : "rejected", error.message);
  }
}

async function main(input) {
  if (input.mode === "semantic_write") {
    // The SDK logs timing data to stdout while saving. Keep it off the wire
    // so that the Python parent receives exactly one protocol JSON object.
    return new Promise((resolve, reject) => {
      const worker = new Worker(__filename, { workerData: input, stdout: true, stderr: true });
      worker.stdout.resume();
      worker.stderr.resume();
      worker.once("message", resolve);
      worker.once("error", reject);
      worker.once("exit", code => reject(new Error(`画布命令未返回结果（退出码 ${code}）`)));
    });
  }
  const loaded = official(input.canvas_entry);
  if (input.mode === "compile") return compile(input, loaded);
  if (input.mode === "snapshot") return snapshot(input, loaded);
  throw new Error("未知画布适配器操作");
}

module.exports = { PROTOCOL, compile, localRuntime, official, main, semanticWrite };
if (!isMainThread && require.main === module) {
  semanticWrite(workerData, official(workerData.canvas_entry))
    .then(result => parentPort.postMessage(result));
} else if (require.main === module) {
  Promise.resolve().then(() => {
    const file = process.argv[2];
    if (!file || process.argv.length !== 3) throw new Error("Usage: node canvas_cli_batch.js INPUT.json");
    return main(JSON.parse(fs.readFileSync(file, "utf8")));
  }).then(data => process.stdout.write(JSON.stringify({ ok: true, data }) + "\n"))
    .catch(error => { process.stdout.write(JSON.stringify({ ok: false, code: "BATCH_ADAPTER_FAILED", message: error.message }) + "\n"); process.exitCode = 1; });
}

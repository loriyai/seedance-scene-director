#!/usr/bin/env node
"use strict";

// Adapter to the installed official SDK. Never rewrites or monkey-patches the CLI.
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

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

async function main(input) {
  const loaded = official(input.canvas_entry);
  if (input.mode === "compile") return compile(input, loaded);
  if (input.mode === "snapshot") return snapshot(input, loaded);
  throw new Error("批量适配器只接受 compile 或 snapshot");
}

module.exports = { PROTOCOL, compile, localRuntime, official, main };
if (require.main === module) {
  Promise.resolve().then(() => {
    const file = process.argv[2];
    if (!file || process.argv.length !== 3) throw new Error("Usage: node canvas_cli_batch.js INPUT.json");
    return main(JSON.parse(fs.readFileSync(file, "utf8")));
  }).then(data => process.stdout.write(JSON.stringify({ ok: true, data }) + "\n"))
    .catch(error => { process.stdout.write(JSON.stringify({ ok: false, code: "BATCH_ADAPTER_FAILED", message: error.message }) + "\n"); process.exitCode = 1; });
}

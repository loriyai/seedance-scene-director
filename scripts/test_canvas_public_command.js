"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { assertExpected, compareObserved, guardedSdk, main, readRecovery, retryRead, retryableRead, guardedPersistence,
  requiredAssetRequests } = require("./canvas_public_command");

function fixture() {
  const document = { rootCanvasId: "root", assets: {
    root: { type: "canvas", content: { nodes: { node1: { id: "node1", type: "biz/role" } }, edges: {} } },
    node1: { type: "biz/role", content: { basicInfo: { name: "Test", appearanceName: "" }, generation: { prompt: "Original", references: [] } } },
  } };
  const expected = [{ node_id: "node1", node: structuredClone(document.assets.root.content.nodes.node1), asset: structuredClone(document.assets.node1) }];
  return { document, expected };
}

const { document, expected } = fixture();
assertExpected(document, "root", expected);
document.assets.node1.assetExtra = { asset_content_version: 2 };
assertExpected(document, "root", expected);
document.assets.node1.assetExtra.businessFlag = "user data";
assert.throws(() => assertExpected(document, "root", expected), error => error.code === "CONCURRENT_EDIT");
delete document.assets.node1.assetExtra;
delete document.assets.node1.content.basicInfo.appearanceName;
delete document.assets.node1.content.generation.references;
assertExpected(document, "root", expected);
document.assets.node1.content.generation.prompt = "Concurrent user change";
assert.throws(() => assertExpected(document, "root", expected), error => error.code === "CONCURRENT_EDIT" && error.notSent);
delete document.assets.node1;
assert.throws(() => assertExpected(document, "root", expected), error => error.code === "READBACK_PENDING" && error.notSent);
assert.throws(() => assertExpected(document, "root", [{ node_id: "node1", node: null, asset: null }]), error => error.notSent);

const partial = fixture();
delete partial.document.assets.node1.content.generation.prompt;
assert.throws(() => assertExpected(partial.document, "root", partial.expected), error => error.code === "READBACK_PENDING" && error.notSent);
partial.document.assets.node1.content.generation.prompt = "";
assert.throws(() => assertExpected(partial.document, "root", partial.expected), error => error.code === "CONCURRENT_EDIT" && error.notSent);
partial.document.assets.node1 = null;
assert.throws(() => assertExpected(partial.document, "root", partial.expected), error => error.code === "CONCURRENT_EDIT" && error.notSent);
assert.equal(compareObserved([{ type: "image" }], [{ type: "image", assetId: "media1" }]), "unknown");
assert.equal(compareObserved([{ type: "image", assetId: "" }], [{ type: "image", assetId: "media1" }]), "conflict");
assert.equal(compareObserved([], [{ assetId: "media1" }]), "conflict");

const source = fixture();
source.document.assets.root.content.nodes.node1.position = { x: 5, y: 20 };
assertExpected(source.document, "root", source.expected.map(item => ({ ...item, source_only: true })));
assert.throws(() => assertExpected(source.document, "root", source.expected), error => error.code === "CONCURRENT_EDIT");
source.document.assets.node1.content.generation.prompt = "source changed";
assert.throws(() => assertExpected(source.document, "root", source.expected.map(item => ({ ...item, source_only: true }))), error => error.code === "CONCURRENT_EDIT");

const identity = fixture();
identity.document.assets.node1.pippitAssetId = "node1";
identity.expected[0].asset.pippitAssetId = "node1";
identity.expected[0].identity_only = true;
identity.document.assets.root.content.nodes.node1.data = { name: "User renamed source" };
identity.document.assets.root.content.nodes.node1.x = 500;
identity.document.assets.node1.content.basicInfo.name = "User renamed asset";
identity.document.assets.node1.content.generation.prompt = "User edited source prompt";
identity.document.assets.node1.content.assetId = "NEW-UPLOADED-MEDIA";
assertExpected(identity.document, "root", identity.expected);
assert.deepEqual(requiredAssetRequests({ state: identity.document, assetVersions: { root: 1, node1: 2 } },
  { canvas_id: "root", expected_nodes: identity.expected }), []);
// The bound source's old content is neither compared nor restored.
identity.document.assets.node1.content = {};
assertExpected(identity.document, "root", identity.expected);
delete identity.document.assets.node1.pippitAssetId;
assert.throws(() => assertExpected(identity.document, "root", identity.expected), error => error.code === "READBACK_PENDING");
assert.equal(requiredAssetRequests({ state: identity.document, assetVersions: { root: 1, node1: 2 } },
  { canvas_id: "root", expected_nodes: identity.expected }).length, 1);
identity.document.assets.node1.pippitAssetId = "other-node";
assert.throws(() => assertExpected(identity.document, "root", identity.expected), error => error.code === "CONCURRENT_EDIT");
identity.document.assets.node1.pippitAssetId = "node1";
identity.document.assets.node1.type = "biz/video";
assert.throws(() => assertExpected(identity.document, "root", identity.expected), error => error.code === "CONCURRENT_EDIT");
identity.document.assets.node1.type = "biz/role";
delete identity.document.assets.node1.content;
assert.throws(() => assertExpected(identity.document, "root", identity.expected), error => error.code === "READBACK_PENDING");
identity.document.assets.node1.content = {};
identity.document.assets.root.content.nodes.video1 = { type: "biz/video" };
identity.document.assets.video1 = { type: "biz/video", content: { generation: { prompt: "Original video" } } };
identity.expected.push({ node_id: "video1", node: { type: "biz/video" }, asset: structuredClone(identity.document.assets.video1) });
assertExpected(identity.document, "root", identity.expected);
identity.document.assets.video1.content.generation.prompt = "Concurrent video edit";
assert.throws(() => assertExpected(identity.document, "root", identity.expected), error => error.code === "CONCURRENT_EDIT" && error.nodeId === "video1");
const legacyIdentity = fixture();
legacyIdentity.expected[0].identity_only = true;
legacyIdentity.document.assets.node1.content.basicInfo.name = "Renamed old bound source";
assertExpected(legacyIdentity.document, "root", legacyIdentity.expected);
legacyIdentity.document.assets.node1.pippitAssetId = "another-source";
assert.throws(() => assertExpected(legacyIdentity.document, "root", legacyIdentity.expected), error => error.code === "CONCURRENT_EDIT");

// The injected definition checks the store used by the actual handler. A second
// preflight snapshot cannot make a changed live store pass.
let actual = fixture();
let calls = 0;
let loaderOptions;
const sdk = {
  createPippitAssetSdkCanvasLoader(options) { loaderOptions = options; return {}; },
  createXyqCanvasOpencodeToolDefinitions() {
    return { apply_mutations: { description: "fixture", execute(value) { calls += 1; assert.equal(value.expectedRevision, 7); return { ok: true }; } } };
  },
};
const observation = {};
const guarded = guardedSdk(sdk, { canvas_id: "root", expected_nodes: actual.expected }, observation);
guarded.createPippitAssetSdkCanvasLoader({ client: {} });
assert.equal(loaderOptions.readOnly, true);
assert.equal(loaderOptions.isLayoutActive(), false);
const definitions = guarded.createXyqCanvasOpencodeToolDefinitions({ runtime: { store: { getState() { return actual.document; }, getRevision() { return 7; } } } });
definitions.apply_mutations.execute({ mutations: [] });
assert.equal(calls, 1);
actual.document.assets.node1.content.generation.prompt = "changed after planning";
assert.throws(() => definitions.apply_mutations.execute({ mutations: [] }), error => error.notSent);
assert.equal(calls, 1);
assert.equal(observation.guardError.code, "CONCURRENT_EDIT");

const incoming = fixture();
incoming.document.assets.root.content.edges.extra = { source: "source2", target: "node1", type: "reference" };
assert.throws(() => assertExpected(incoming.document, "root", incoming.expected, [], ["node1"]),
  error => error.code === "CONCURRENT_EDIT");
for (const status of [429, 502, 503, 504]) assert.equal(retryableRead(new Error(`HTTP ${status}`)), true);
for (const status of [401, 403]) assert.equal(retryableRead(new Error(`HTTP ${status}`)), false);

const rebaseState = fixture();
const rebaseProject = fs.mkdtempSync(path.join(os.tmpdir(), "seedance-rebase-wrapper-"));
const rebaseObservation = { checked: true };
let priorValidation = 0;
const rebaseWrapped = guardedSdk({ ...sdk, createXyqCanvasCommandRuntime: options => options },
  { canvas_id: "root", expected_nodes: rebaseState.expected, project: rebaseProject }, rebaseObservation);
const rebaseRuntime = rebaseWrapped.createXyqCanvasCommandRuntime({ sync: {
  flush: { maxAttempts: 1 }, validateRebasedEnvelope() { priorValidation += 1; },
} });
assert.equal(rebaseRuntime.sync.flush.maxAttempts, 1);
rebaseRuntime.sync.validateRebasedEnvelope({ previousDocument: rebaseState.document });
assert.equal(priorValidation, 1);
rebaseState.document.assets.node1.content.generation.prompt = "concurrent edit during flush";
assert.throws(() => rebaseRuntime.sync.validateRebasedEnvelope({ previousDocument: rebaseState.document, envelope: { txId: "fixture-rebase", patches: [] } }),
  error => error.code === "CONCURRENT_EDIT" && error.notSent === false);
assert.equal(priorValidation, 1);
const refused = rebaseWrapped.createXyqCanvasOpencodeToolDefinitions({ runtime: {
  store: { getState() { return fixture().document; }, getRevision() { return 7; } },
} });
const beforeRejectedHandler = calls;
assert.throws(() => refused.apply_mutations.execute({}), error => error === rebaseObservation.rebaseError);
assert.equal(calls, beforeRejectedHandler);
fs.rmSync(rebaseProject, { recursive: true, force: true });

const reference = fixture();
reference.document.assets.root.content.nodes.node1.type = "biz/video";
reference.document.assets.root.content.nodes.source1 = { type: "biz/image" };
reference.document.assets.node1.type = "biz/video";
reference.document.assets.node1.content.generation.references = [{ sourceNodeId: "source1", type: "image",
  assetId: "old-media", label: "Old source name", edgeId: "edge1" }];
reference.expected[0] = { node_id: "node1", node: structuredClone(reference.document.assets.root.content.nodes.node1),
  asset: structuredClone(reference.document.assets.node1), reference_identity_only: true };
const referenceInput = { canvas_id: "root", expected_nodes: reference.expected, name: "xyq.generation.update_prompt" };
const referenceOptions = { runtime: { store: {
  getState() { return reference.document; }, getRevision() { return 7; },
} } };
reference.document.assets.node1.content.generation.references[0] = { nodeAssetId: "source1", type: "image",
  assetId: "user-uploaded-media", label: "User renamed source", edgeId: "new-runtime-edge", metadata: { caption: "User caption" } };
const referenceBefore = calls;
guardedSdk(sdk, referenceInput, {}).createXyqCanvasOpencodeToolDefinitions(referenceOptions).apply_mutations.execute({});
assert.equal(calls, referenceBefore + 1);
// A marker must not weaken an ordinary update that would send a stale entire
// generation object (including its old references array) back to the server.
const staleWholeGeneration = { mutations: [{ kind: "update_node_data", nodeId: "node1",
  data: { generation: structuredClone(reference.expected[0].asset.content.generation) } }] };
assert.throws(() => guardedSdk(sdk, { ...referenceInput, name: "apply_mutations" }, {})
  .createXyqCanvasOpencodeToolDefinitions(referenceOptions).apply_mutations.execute(staleWholeGeneration),
error => error.code === "CONCURRENT_EDIT");
assert.equal(calls, referenceBefore + 1);
const referenceRebase = guardedSdk({ ...sdk, createXyqCanvasCommandRuntime: options => options },
  referenceInput, { checked: true }).createXyqCanvasCommandRuntime({});
referenceRebase.sync.validateRebasedEnvelope({ previousDocument: reference.document });
reference.document.assets.node1.content.generation.durationMs = 9000;
assert.throws(() => guardedSdk(sdk, referenceInput, {}).createXyqCanvasOpencodeToolDefinitions(referenceOptions)
  .apply_mutations.execute({}), error => error.code === "CONCURRENT_EDIT");
delete reference.document.assets.node1.content.generation.durationMs;
reference.document.assets.node1.content.generation.references[0].nodeAssetId = "different-source";
assert.throws(() => guardedSdk(sdk, referenceInput, {}).createXyqCanvasOpencodeToolDefinitions(referenceOptions)
  .apply_mutations.execute({}), error => error.code === "CONCURRENT_EDIT");
delete reference.document.assets.node1.content.generation.references[0].nodeAssetId;
assert.throws(() => guardedSdk(sdk, referenceInput, {}).createXyqCanvasOpencodeToolDefinitions(referenceOptions)
  .apply_mutations.execute({}), error => error.code === "READBACK_PENDING");

// Exercise the actual 1.0.57 loader/transport against an in-memory native
// reader. No login, requests, asset allocation or remote writes are involved.
async function officialLoaderTest() {
  const entry = process.env.SEEDANCE_TEST_CANVAS_ENTRY || (process.env.APPDATA &&
    path.join(process.env.APPDATA, "npm/node_modules/@pippit-dev/cli/scripts/run.js"));
  if (!entry || !fs.existsSync(entry)) return;
  const packageRoot = path.resolve(path.dirname(entry), "..");
  if (JSON.parse(fs.readFileSync(path.join(packageRoot, "package.json"))).version !== "1.0.57") return;
  const official = require(path.join(packageRoot, "scripts/canvas-command.js"));
  const officialSdk = require(path.join(packageRoot, "dist/xyq-canvas-command-runtime.cjs"));
  official.ensureNodeRuntimeGlobals();
  const root = "99100000000001", node = "99100000000002";
  const rootAsset = { pippitAssetId: root, type: "canvas", content: { nodes: {
    [node]: { id: node, type: "biz/role", x: 0, y: 0, w: 200, h: 200, parentId: null, order: 0, data: { name: "Fixture" } },
  }, edges: {}, settings: {} }, extra: {} };
  const asset = { pippitAssetId: node, type: "biz/role", content: { nodeName: "Fixture",
    basicInfo: { name: "Fixture" }, generation: { prompt: "Before" } }, extra: {} };
  const reads = [];
  let omitted = false;
  const wrapped = guardedSdk(officialSdk, { canvas_id: root,
    expected_nodes: [{ node_id: node, node: rootAsset.content.nodes[node], asset }] }, {},
  readRecovery({ delay: async () => {} }));
  const runtime = official.createCanvasAssetRuntime({ sdk: wrapped, nativeClient: {
    async getAssetsAllowMissing(ids) {
      reads.push([...ids]);
      if (ids.includes(node) && !omitted) { omitted = true; return []; }
      return ids.map(id => ({ PippitAssetID: id, AssetType: 1, version: id === root ? 7 : 11,
        Extra: { asset_content_version: id === root ? "7" : "11" },
        TextInfo: { content: JSON.stringify(id === root ? rootAsset : asset) } }));
    },
    apply() { assert.fail("Offline loader must never write"); },
    allocateAssetIds() { assert.fail("Offline loader must never allocate"); },
  } });
  const transport = runtime.assetRuntime.createSyncTransport({ clientId: "offline-fixture", loader: runtime.loader, scopeAssetId: root });
  try {
    const result = await transport.fetchSnapshot();
    assert.ok(result.state.assets[node]);
    assert.equal(transport.assetVersions.get(root), 7);
    assert.equal(transport.assetVersions.get(node), 11);
    assert.deepEqual(reads, [[root], [node], [node]]);
  } finally { transport.close(); runtime.assetRuntime.dispose(); }
}

async function isolatedPersistenceTest() {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "seedance-outbox-"));
  try {
    const envelope = { txId: "prior-operation-tx", patches: [{ op: "replace", path: "/content/generation/prompt", value: "after" }] };
    let quarantined = 0;
    const persistence = { statePath: path.join(directory, "state.json"),
      async loadOutbound() { return [envelope]; },
      async quarantine() { quarantined += 1; return path.join(directory, "state.ambiguous.json"); },
    };
    const observation = {};
    const known = guardedPersistence(persistence, observation, { knownOperation: true, stateDirectory: directory });
    await assert.rejects(known.loadOutbound(), error => error.code === "READBACK_PENDING" && error.notSent === false);
    assert.equal(quarantined, 1);
    assert.equal(observation.bootstrapError.recoveryPath, path.join(directory, "state.ambiguous.json"));
    const unknown = guardedPersistence(persistence, {}, { knownOperation: false, stateDirectory: directory });
    await assert.rejects(unknown.loadOutbound(), error => error.notSent === false);
    assert.equal(quarantined, 1);
    const different = guardedPersistence(persistence, {}, { knownOperation: true, stateDirectory: path.join(directory, "another-operation") });
    await assert.rejects(different.loadOutbound(), error => error.notSent === false);
    assert.equal(quarantined, 1);
    persistence.loadOutbound = async () => [];
    assert.deepEqual(await known.loadOutbound(), []);
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
}

async function targetedLoaderTest() {
  const state = fixture();
  const incomplete = { state: structuredClone(state.document), assetVersions: { root: 4 } };
  delete incomplete.state.assets.node1;
  const delays = [];
  let targeted = 0;
  let optionsSeen;
  const mock = { createPippitAssetSdkCanvasLoader(options) {
    optionsSeen = options;
    return {
      async fetchSnapshot() { return incomplete; },
      async fetchAssets(request) {
        targeted += 1;
        assert.equal(request.scopeAssetId, "root");
        assert.deepEqual(request.requests, [{ assetId: "node1", assetKind: "node", nodeType: "biz/role" }]);
        if (targeted === 1) throw new Error("read tcp wsarecv: An existing connection was forcibly closed by the remote host");
        return { assets: [{ assetId: "node1", asset: state.document.assets.node1, assetVersion: 9 }], assetVersions: { node1: 9 } };
      },
    };
  } };
  const input = { canvas_id: "root", expected_nodes: state.expected };
  const wrapped = guardedSdk(mock, input, {}, readRecovery({ delay: async value => delays.push(value) }));
  const result = await wrapped.createPippitAssetSdkCanvasLoader({ client: {} }).fetchSnapshot({ scopeAssetId: "root" });
  assert.deepEqual(result.state, state.document);
  assert.deepEqual(result.assetVersions, { root: 4, node1: 9 });
  assert.equal(Object.hasOwn(incomplete.state.assets, "node1"), false);
  assert.equal(targeted, 2);
  assert.deepEqual(delays, [1000]);
  assert.deepEqual(optionsSeen.resolvePriorityNodeAssetIds(state.document), ["node1"]);
  assert.equal(optionsSeen.readOnly, true);
  assert.equal(optionsSeen.isLayoutActive(), false);

  let missingVersions = 0;
  mock.createPippitAssetSdkCanvasLoader = () => ({
    async fetchSnapshot() { return incomplete; },
    async fetchAssets() { missingVersions += 1; return { assets: [{ assetId: "node1", asset: state.document.assets.node1 }] }; },
  });
  await assert.rejects(guardedSdk(mock, input, {}, readRecovery({ delay: async () => {} }))
    .createPippitAssetSdkCanvasLoader({}).fetchSnapshot({ scopeAssetId: "root" }),
  error => error.code === "READBACK_PENDING" && error.notSent);
  assert.equal(missingVersions, 3);

  // Retry only the read callback, never mutation allocation or application.
  let reads = 0;
  const mutation = { batchPatchAsset() { assert.fail("The read retry must not call a writer"); } };
  const transport = guardedSdk({ createPippitAssetServiceTransport: options => options }, input, {},
    readRecovery({ delay: async () => {} })).createPippitAssetServiceTransport({ mutation,
    async assetQuery() { reads += 1; if (reads < 3) throw new Error("unexpected EOF"); return { data: { Assets: [] } }; },
  });
  await transport.assetQuery({ pippit_asset_ids: ["node1"] });
  assert.equal(reads, 3);
  assert.equal(transport.mutation, mutation);
  let authReads = 0;
  await assert.rejects(retryRead(async () => { authReads += 1; throw new Error("403 Forbidden"); },
    readRecovery({ delay: async () => assert.fail("Authorization failures do not retry") })), /403/);
  assert.equal(authReads, 1);
  let budgetReads = 0;
  const exhausted = readRecovery({ now: () => 0, delay: async () => assert.fail("Expired retry budget") });
  exhausted.now = () => 60000;
  await assert.rejects(retryRead(async () => { budgetReads += 1; throw new Error("EOF"); }, exhausted), /EOF/);
  assert.equal(budgetReads, 1);
}

async function publicEntryTest() {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "seedance-public-guard-"));
  try {
    fs.mkdirSync(path.join(temp, "scripts"));
    fs.writeFileSync(path.join(temp, "package.json"), JSON.stringify({ version: "1.0.57" }));
    const state = fixture();
    const input = { name: "apply_mutations", input: { atomic: true, mutations: [] },
      canvas_entry: path.join(temp, "scripts", "run.js"), canvas_id: "root",
      team_id: "0", cli_path: "unused", project: temp, expected_nodes: state.expected, operation_id: "fixture-operation" };
    let incomplete = false;
    let attempts = 0;
    const delays = [];
    const stateDirectories = new Set();
    const helper = {
      ensureNodeRuntimeGlobals() {},
      async runCanvasCommand(args, options) {
        attempts += 1;
        stateDirectories.add(options.stateDirectory);
        assert.equal(path.dirname(options.stateDirectory), path.join(temp, "canvas", "cli-state"));
        assert.equal(args[3], "apply_mutations");
        const tools = options.sdk.createXyqCanvasOpencodeToolDefinitions({ runtime: {
          store: { getState() { return state.document; }, getRevision() { return 7; } },
        } });
        if (incomplete) delete state.document.assets.node1.content.generation.prompt;
        else state.document.assets.node1.content.generation.prompt = "new user edit";
        try { tools.apply_mutations.execute({}); }
        catch (error) { throw new Error(error.message + " public helper wrapping"); }
      },
    };
    const countBefore = calls;
    const { result, code } = await main(input, { sdk, helper });
    assert.equal(code, 1);
    assert.equal(result.code, "CONCURRENT_EDIT");
    assert.equal(result.not_sent, true);
    assert.equal(calls, countBefore);
    incomplete = true;
    const pending = await main(input, { sdk, helper, delay: async value => delays.push(value) });
    assert.equal(pending.result.code, "READBACK_PENDING");
    assert.equal(pending.result.not_sent, true);
    assert.equal(pending.result.attempts, 3);
    assert.equal(attempts, 4);
    assert.deepEqual(delays, [1000, 2000]);
    assert.equal(stateDirectories.size, 1);
    assert.equal(calls, countBefore);
    state.document.assets.node1.content.generation.prompt = "Original";
    let readFailures = 0;
    const readDelays = [];
    const recovered = await main(input, { sdk, delay: async value => readDelays.push(value), helper: {
      ensureNodeRuntimeGlobals() {}, async runCanvasCommand(args, options) {
        readFailures += 1;
        if (readFailures < 3) throw new Error("canvas get: unexpected EOF");
        const tools = options.sdk.createXyqCanvasOpencodeToolDefinitions({ runtime: {
          store: { getState() { return state.document; }, getRevision() { return 7; } },
        } });
        tools.apply_mutations.execute({});
        options.stdout.write(JSON.stringify({ ok: true }));
        return 0;
      },
    } });
    assert.equal(recovered.result.ok, true);
    assert.equal(readFailures, 3);
    assert.deepEqual(readDelays, [1000, 2000]);
    assert.equal(calls, countBefore + 1);
    const exhausted = await main(input, { sdk, delay: async () => {}, helper: {
      ensureNodeRuntimeGlobals() {}, async runCanvasCommand() { throw new Error("connection reset by peer"); },
    } });
    assert.equal(exhausted.result.code, "READBACK_PENDING");
    assert.equal(exhausted.result.not_sent, true);
    assert.equal(exhausted.result.attempts, 3);
    let dispatched = 0;
    await assert.rejects(main(input, { sdk, delay: async () => assert.fail("A dispatched write must never be retried"),
      helper: { ensureNodeRuntimeGlobals() {}, async runCanvasCommand(args, options) {
        dispatched += 1;
        const tools = options.sdk.createXyqCanvasOpencodeToolDefinitions({ runtime: {
          store: { getState() { return state.document; }, getRevision() { return 7; } },
        } });
        tools.apply_mutations.execute({});
        throw new Error("Lost reply after handler");
      } },
    }), /Lost reply after handler/);
    assert.equal(dispatched, 1);
    assert.equal(calls, countBefore + 2);
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
}
Promise.resolve().then(targetedLoaderTest).then(officialLoaderTest).then(isolatedPersistenceTest).then(publicEntryTest)
  .then(() => console.log("Canvas public-command precondition tests passed"))
  .catch(error => { console.error(error); process.exitCode = 1; });

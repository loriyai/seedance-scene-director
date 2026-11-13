"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { assertExpected, compareObserved, guardedSdk, main } = require("./canvas_public_command");

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

async function publicEntryTest() {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "seedance-public-guard-"));
  try {
    fs.mkdirSync(path.join(temp, "scripts"));
    fs.writeFileSync(path.join(temp, "package.json"), JSON.stringify({ version: "1.0.57" }));
    const state = fixture();
    const input = { name: "apply_mutations", input: { atomic: true, mutations: [] },
      canvas_entry: path.join(temp, "scripts", "run.js"), canvas_id: "root",
      team_id: "0", cli_path: "unused", project: temp, expected_nodes: state.expected };
    let incomplete = false;
    let attempts = 0;
    const delays = [];
    const helper = {
      ensureNodeRuntimeGlobals() {},
      async runCanvasCommand(args, options) {
        attempts += 1;
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
    assert.equal(calls, countBefore);
    state.document.assets.node1.content.generation.prompt = "Original";
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
    assert.equal(calls, countBefore + 1);
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
}
publicEntryTest().then(() => console.log("Canvas public-command precondition tests passed"))
  .catch(error => { console.error(error); process.exitCode = 1; });

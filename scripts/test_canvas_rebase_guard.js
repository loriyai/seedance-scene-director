"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createRebaseGuard } = require("./canvas_rebase_guard");
const { assertExpected } = require("./canvas_public_command");
const project = fs.mkdtempSync(path.join(os.tmpdir(), "seedance-rebase-"));
process.on("exit", () => {
  if (path.dirname(project) === path.resolve(os.tmpdir()) && path.basename(project).startsWith("seedance-rebase-")) {
    fs.rmSync(project, { recursive: true, force: true });
  }
});

function fixture() {
  const document = { rootCanvasId: "root", assets: {
    root: { type: "canvas", content: { nodes: { node1: { type: "biz/video", data: {} } }, edges: {} } },
    node1: { type: "biz/video", content: { generation: { prompt: "before" } } },
  } };
  const input = { project, name: "apply_mutations", canvas_id: "root", expected_nodes: [{ node_id: "node1",
    node: structuredClone(document.assets.root.content.nodes.node1), asset: structuredClone(document.assets.node1) }],
    expected_edges: [], expected_edge_targets: ["node1"] };
  const observation = { checked: true };
  return { document, observation, guard: createRebaseGuard({ input, observation, assertExpected }) };
}

const original = fixture();
original.guard({ previousDocument: original.document, nextDocument: {}, envelope: { txId: "tx_original" } });
assert.equal(original.observation.rebaseError, undefined);
assert.equal(original.observation.checked, true);

const historical = fixture();
historical.observation.checked = false;
assert.throws(() => historical.guard({ previousDocument: historical.document, envelope: { txId: "tx_prior_process" } }), error =>
  error.code === "READBACK_PENDING" && error.notSent === false && error.transactionId === "tx_prior_process");
assert.equal(historical.observation.checked, false);
assert.equal(historical.observation.rebaseError.phase, "rebase");
const recovered = JSON.parse(fs.readFileSync(historical.observation.rebaseRecoveryPath, "utf8"));
assert.equal(recovered.transaction_id, "tx_prior_process");
assert.deepEqual(recovered.envelope, { txId: "tx_prior_process" });
assert.equal(recovered.handler_entered, false);
assert.equal(recovered.not_sent, false);

const partial = fixture();
delete partial.document.assets.node1.content.generation.prompt;
assert.throws(() => partial.guard({ previousDocument: partial.document, envelope: { txId: "tx_unknown" } }), error =>
  error.code === "READBACK_PENDING" && error.notSent === false && error.not_sent === false && error.phase === "rebase");
assert.equal(partial.observation.rebaseError.transactionId, "tx_unknown");

const changed = fixture();
changed.document.assets.node1.content.generation.prompt = "concurrent edit";
assert.throws(() => changed.guard({ previousDocument: changed.document, envelope: { txId: "tx_conflict" } }), error =>
  error.code === "CONCURRENT_EDIT" && error.notSent === false);
assert.equal(changed.observation.rebaseError.transactionId, "tx_conflict");

const manualReference = fixture();
manualReference.document.assets.root.content.edges.manual = { id: "manual", type: "reference", source: "source2", target: "node1" };
assert.throws(() => manualReference.guard({ previousDocument: manualReference.document, envelope: { txId: "tx_new_incoming" } }), error =>
  error.code === "CONCURRENT_EDIT" && error.notSent === false && error.nodeId === "node1");
assert.equal(manualReference.observation.rebaseError.transactionId, "tx_new_incoming");

// Even an apparent rebase noop must be reconciled by the caller's full target
// verifier; this guard cannot infer that an unrelated new value is our target.
const noOp = fixture();
noOp.document.assets.node1.content.generation.prompt = "after";
assert.throws(() => noOp.guard({ previousDocument: noOp.document, nextDocument: structuredClone(noOp.document),
  envelope: { txId: "tx_noop" } }), error => error.code === "CONCURRENT_EDIT" && error.notSent === false);

const unexpectedObservation = { checked: true };
const unexpected = createRebaseGuard({ input: { project, canvas_id: "root" }, observation: unexpectedObservation,
  assertExpected() { throw new TypeError("Malformed partial snapshot"); } });
assert.throws(() => unexpected({ envelope: { txId: "tx_malformed" } }), error => error.code === "READBACK_PENDING" && error.notSent === false);
assert.equal(unexpectedObservation.rebaseError.phase, "rebase");
const failedEvidence = createRebaseGuard({ input: {}, observation: {}, assertExpected });
assert.throws(() => failedEvidence({ envelope: { txId: "tx_unarchivable" } }), error =>
  error.code === "READBACK_PENDING" && error.notSent === false && error.cause.code === "READBACK_PENDING");
console.log("Canvas rebase precondition tests passed");

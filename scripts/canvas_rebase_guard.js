"use strict";
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

function saveRejectedEnvelope(input, observation, envelope, error) {
  if (typeof input.project !== "string" || !input.project || typeof envelope?.txId !== "string") {
    throw new Error("恢复证据缺少项目路径或事务 ID");
  }
  const serialized = JSON.stringify(envelope);
  const digest = crypto.createHash("sha256").update(JSON.stringify([input.canvas_id, serialized])).digest("hex");
  const directory = path.join(path.resolve(input.project), "canvas", "recovery");
  const destination = path.join(directory, `rebase-${digest}.json`);
  const record = { schema: 1, envelope_sha256: digest, canvas_id: input.canvas_id,
    command: input.name, transaction_id: envelope.txId, phase: "rebase", code: error.code,
    handler_entered: observation.checked === true, not_sent: false, envelope: JSON.parse(serialized) };
  fs.mkdirSync(directory, { recursive: true });
  if (fs.existsSync(destination)) {
    const existing = JSON.parse(fs.readFileSync(destination, "utf8"));
    if (existing.envelope_sha256 !== digest || existing.transaction_id !== envelope.txId) {
      throw new Error("已有恢复证据与事务不一致");
    }
    return destination;
  }
  const temporary = path.join(directory, `.${digest}.${process.pid}.${crypto.randomBytes(6).toString("hex")}.tmp`);
  let descriptor;
  try {
    descriptor = fs.openSync(temporary, "wx", 0o600);
    fs.writeFileSync(descriptor, JSON.stringify(record) + "\n", "utf8");
    fs.fsyncSync(descriptor);
    fs.closeSync(descriptor);
    descriptor = undefined;
    fs.renameSync(temporary, destination);
  } finally {
    if (descriptor !== undefined) fs.closeSync(descriptor);
    try { fs.unlinkSync(temporary); } catch (cleanupError) { if (cleanupError.code !== "ENOENT") throw cleanupError; }
  }
  return destination;
}

// A public SDK command may be rebased after its local handler completed. Keep
// that rebase bound to the original business preconditions; a fresh asset
// version alone must not authorize overwriting a concurrent edit.
function createRebaseGuard({ input, observation, assertExpected }) {
  if (typeof assertExpected !== "function") throw new TypeError("assertExpected must be a function");
  return function validateRebasedEnvelope({ previousDocument, envelope }) {
    try {
      // A prior process may have died before the CLI could quarantine its
      // outbox. Bootstrapping must not replay that older envelope before this
      // command's guard has admitted its own handler.
      if (observation.checked !== true) {
        const error = new Error("发现旧会话未确认事务，禁止启动时重放；需先回读原目标。");
        error.code = "READBACK_PENDING";
        throw error;
      }
      assertExpected(previousDocument, input.canvas_id, input.expected_nodes, input.expected_edges,
        input.expected_edge_targets);
    } catch (cause) {
      let error = cause instanceof Error ? cause : new Error(String(cause));
      if (!["READBACK_PENDING", "CONCURRENT_EDIT"].includes(error.code)) error.code = "READBACK_PENDING";
      // The original handler or an earlier transport attempt may have run.
      // This is never proof that the original command was not sent.
      error.notSent = false;
      error.not_sent = false;
      error.phase = "rebase";
      if (typeof envelope?.txId === "string") error.transactionId = envelope.txId;
      try {
        error.recoveryPath = saveRejectedEnvelope(input, observation, envelope, error);
        observation.rebaseRecoveryPath = error.recoveryPath;
      } catch (evidenceError) {
        const original = error;
        error = new Error(`重基线核验被拒绝，恢复证据保存失败：${evidenceError.message}`);
        error.code = "READBACK_PENDING";
        error.originalCode = original.code;
        error.cause = original;
        error.notSent = false;
        error.not_sent = false;
        error.phase = "rebase";
        error.transactionId = original.transactionId;
      }
      observation.rebaseError = error;
      throw error;
    }
  };
}

module.exports = { createRebaseGuard, saveRejectedEnvelope };

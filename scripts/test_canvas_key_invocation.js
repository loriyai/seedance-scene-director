"use strict";
const assert = require("node:assert/strict");
const { spawnSync } = require("child_process");
const { nativeInvocation, safeDiagnostic } = require("./canvas_key_invocation");

const browser = nativeInvocation("official-cli", {});
assert.equal(browser.command, "official-cli");
assert.equal(browser.prefixArgs, undefined);
const key = nativeInvocation("official-cli", { XYQ_ACCESS_KEY: "test-only", PIPPIT_CLI_CANVAS_TRANSPORT: "unexpected" });
assert.equal(key.command, process.execPath);
assert.equal(key.prefixArgs[1], "official-cli");
assert.equal(key.env.PIPPIT_CLI_CANVAS_TRANSPORT, undefined);
assert.equal(key.env.XYQ_ACCESS_KEY, "test-only");
const guard = key.prefixArgs[0];
for (const command of ["login", "logout"]) {
  const result = spawnSync(process.execPath, [guard, process.execPath, command], { encoding: "utf8" });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /不自动打开网页登录/);
}
const forwarded = spawnSync(process.execPath, [guard, process.execPath, "--version"], { encoding: "utf8" });
assert.equal(forwarded.status, 0);
assert.equal(forwarded.stdout.trim(), process.version);
const previousKey = process.env.XYQ_ACCESS_KEY;
process.env.XYQ_ACCESS_KEY = "test-only-key-never-log";
assert.equal(safeDiagnostic(new Error("HTTP 401 test-only-key-never-log")), "HTTP 401 [隐藏]");
assert.match(safeDiagnostic(new Error("authorization=other-test-token")), /authorization=\[隐藏\]/);
if (previousKey === undefined) delete process.env.XYQ_ACCESS_KEY;
else process.env.XYQ_ACCESS_KEY = previousKey;
console.log("Pure Key invocation restriction checks passed.");

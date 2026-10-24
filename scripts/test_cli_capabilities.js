#!/usr/bin/env node
// Exercise capability discovery with a fake CLI; never install or contact a service.
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const source = fs.readFileSync(path.join(__dirname, "ensure-cli.js"), "utf8");
const requiredSemantic = [
  "get_snapshot", "get_permissions", "create_biz_node", "update_node_data", "update_asset", "delete_nodes", "delete_edges",
  "xyq.generation.update_prompt", "xyq.role.updateDescription",
  "xyq.scene.updateDescription", "xyq.role.updateAppearance",
];
const cliDir = path.resolve("cli-capability-fixture");
const cliPath = path.join(cliDir, `pippit-tool-cli${process.platform === "win32" ? ".exe" : ""}`);
const entry = path.resolve(path.dirname(cliPath), "../scripts/run.js");

function fixture(catalogNames, promptContract = true, teamFlag = true) {
  const calls = [];
  const messages = [];
  const fakeFs = {
    existsSync: (value) => value === cliPath || value === entry || value.endsWith(path.join("npm", "bin", "npm-cli.js")),
    statSync: () => ({ isFile: () => true }),
    realpathSync: (value) => value,
    mkdirSync: () => {},
    mkdtempSync: (value) => value + "mock",
    rmSync: () => {},
  };
  const fakeExec = (command, args) => {
    calls.push({ command, args });
    if (args.includes("install")) {
      const error = new Error("mock refuses all installation");
      error.status = 1;
      throw error;
    }
    if (args.length === 1 && args[0] === "--version") return Buffer.from("1.0.38");
    if (args.includes("--help")) return Buffer.from(teamFlag ? "supported --team-id ID" : "supported");
    if (args.slice(-3).join(" ") === "canvas command list") {
      return Buffer.from(JSON.stringify({ commands: catalogNames.map((name) => ({ name })) }));
    }
    if (args.slice(-4).join(" ") === "canvas command schema xyq.role.updateAppearance") {
      return Buffer.from(JSON.stringify({input_schema: {properties: {patch: {properties: promptContract ? {generationPrompt: {}} : {}}, expected: {properties: {generationPrompt: {}}}}}}));
    }
    throw new Error("unexpected mock command: " + args.join(" "));
  };
  const module = { exports: {} };
  const context = {
    module,
    require: (name) => name === "child_process" ? { execFileSync: fakeExec }
      : name === "fs" ? fakeFs : name === "os" ? { homedir: () => cliDir } : require(name),
    process: { platform: process.platform, arch: process.arch, versions: process.versions,
      execPath: process.execPath, env: { PATH: cliDir } },
    console: { error: (value) => messages.push(value), log: () => {} },
    Buffer,
  };
  vm.runInNewContext(source, context, { filename: "ensure-cli.js" });
  return { ensureCLI: module.exports.ensureCLI, calls, messages };
}

let passedCases = 0;
function runCase(name, check) {
  try {
    check();
    passedCases += 1;
  } catch (error) {
    console.error(`CLI capability case failed: ${name}`);
    throw error;
  }
}

runCase("complete canvas command catalog", () => {
  const normal = fixture(requiredSemantic);
  assert.strictEqual(normal.ensureCLI({ canvas: true }).cli_path, cliPath);
  const probes = normal.calls.filter((call) => call.args.includes("--help")).map((call) => call.args.slice(0, -1).join(" "));
  assert.deepStrictEqual(probes, ["status", "login", "canvas get", "canvas allocate", "canvas upload", "space list"]);
  assert(!normal.calls.some((call) => call.args.includes("install")));
});

for (const missing of requiredSemantic) {
  runCase(`missing canvas command: ${missing}`, () => {
    const test = fixture(requiredSemantic.filter((name) => name !== missing));
    assert.throws(() => test.ensureCLI({ canvas: true }));
    assert(test.messages.some((message) => message.includes("canvas command runtime")));
  });
}

runCase("base authentication without canvas commands", () => {
  const base = fixture([]);
  assert.strictEqual(base.ensureCLI().cli_path, cliPath);
  assert.deepStrictEqual(base.calls.filter((call) => call.args.includes("--help")).map((call) => call.args.slice(0, -1).join(" ")), ["status", "login"]);
});


runCase("missing independent role prompt schema", () => {
  assert.throws(() => fixture(requiredSemantic, false).ensureCLI({ canvas: true }));
});
runCase("missing team-id flag", () => {
  assert.throws(() => fixture(requiredSemantic, true, false).ensureCLI({ canvas: true }));
});

console.log(`CLI capability regression checks passed (${passedCases} cases, offline mock).`);

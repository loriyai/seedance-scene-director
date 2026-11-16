"use strict";

const path = require("path");
const fs = require("fs");
const { nativeInvocation, safeDiagnostic } = require("./canvas_key_invocation");

async function main() {
  const [cliPath, canvasEntry, ...args] = process.argv.slice(2);
  const root = path.resolve(path.dirname(canvasEntry), "..");
  if (JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8")).version !== "1.0.57") {
    throw new Error("纯 Key 适配器仅支持官方 CLI 1.0.57。");
  }
  const helper = require(path.join(root, "scripts", "canvas-command.js"));
  process.exitCode = await helper.runCanvasCommand(args, { nativeInvocation: nativeInvocation(cliPath) });
}

main().catch(error => { process.stderr.write(safeDiagnostic(error) + "\n"); process.exitCode = 1; });

"use strict";

const { spawn } = require("child_process");

function safeDiagnostic(error) {
  let text = String(error?.message || error);
  const key = process.env.XYQ_ACCESS_KEY;
  if (key) text = text.split(key).join("[隐藏]");
  return text.replace(/((?:access[_-]?key|authorization|token|secret|cookie)["']?\s*[:=]\s*["']?)[^\s,"'}]+/gi, "$1[隐藏]").slice(0, 1600);
}

function nativeInvocation(cliPath, environment = process.env) {
  const env = { ...environment, PIPPIT_CLI_DISABLE_UPDATE_CHECK: "1" };
  delete env.PIPPIT_CLI_CANVAS_TRANSPORT;
  delete env.PIPPIT_CLI_CANVAS_SEMANTIC_COMMAND;
  if (!String(env.XYQ_ACCESS_KEY || "").trim()) return { command: cliPath, env };
  return { command: process.execPath, prefixArgs: [__filename, cliPath], env };
}

function forward(cliPath, args) {
  // This adapter only restricts automatic login. Authentication, commands,
  // transport and server permission checks stay in the official CLI.
  if (!cliPath || args[0] === "login" || args[0] === "logout") {
    process.stderr.write("CLI_AUTH_ERROR unauthorized: 纯 Access Key 模式不自动打开网页登录或更改登录凭据；请核对服务端认证拒绝。\n");
    process.exitCode = 1;
    return;
  }
  const child = spawn(cliPath, args, { env: process.env, stdio: "inherit", shell: false });
  child.once("error", () => { process.stderr.write("官方 CLI 子进程启动失败。\n"); process.exitCode = 1; });
  child.once("exit", (code, signal) => {
    if (signal) process.kill(process.pid, signal);
    else process.exitCode = code ?? 1;
  });
}

if (require.main === module) forward(process.argv[2], process.argv.slice(3));
module.exports = { nativeInvocation, safeDiagnostic };

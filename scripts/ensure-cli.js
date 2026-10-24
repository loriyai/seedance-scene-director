#!/usr/bin/env node

// Self-contained entrypoint shipped inside the Skill ZIP. No npm dependencies.
const { execFileSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const REQUIRED_COMMANDS = [
  "status", "login",
];
const REQUIRED_CANVAS_NATIVE_COMMANDS = [
  "canvas get", "canvas allocate", "canvas upload", "space list",
];
const REQUIRED_CANVAS_COMMANDS = [
  "get_snapshot", "get_permissions", "create_biz_node", "create_edge", "update_node_data", "update_asset", "delete_nodes", "delete_edges",
  "xyq.generation.update_prompt", "xyq.role.updateDescription",
  "xyq.scene.updateDescription", "xyq.role.updateAppearance",
];

function npmCommand() {
  if (process.platform !== "win32") return ["npm", []];
  // Run npm's JS entrypoint directly, avoiding cmd.exe quoting for paths with spaces.
  const dirs = [path.dirname(process.execPath), ...(process.env.PATH || "").split(path.delimiter)];
  for (const dir of dirs.filter(Boolean)) {
    const script = path.join(dir, "node_modules", "npm", "bin", "npm-cli.js");
    if (fs.existsSync(script)) return [process.execPath, [script]];
  }
  throw new Error("未找到 npm，请先安装 Node.js 16+ 和 npm。");
}

function findCLIOnPath() {
  for (const dir of (process.env.PATH || "").split(path.delimiter).filter(Boolean)) {
    const candidate = path.resolve(dir, `pippit-tool-cli${process.platform === "win32" ? ".exe" : ""}`);
    if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) {
      const resolved = fs.realpathSync(candidate);
      // Bypass the npm JS launcher, which may download a binary or check for updates.
      if (resolved.endsWith(path.join("scripts", "run.js"))) {
        const binary = path.resolve(path.dirname(resolved), "../bin/pippit-tool-cli");
        if (fs.existsSync(binary)) return binary;
      } else {
        return candidate;
      }
    }
    if (process.platform === "win32" && fs.existsSync(path.join(dir, "pippit-tool-cli.cmd"))) {
      const binary = path.resolve(dir, "node_modules/@pippit-dev/cli/bin/pippit-tool-cli.exe");
      if (fs.existsSync(binary)) return binary;
    }
  }
  return null;
}

function ensureCLI({ canvas = false } = {}) {
  if (Number(process.versions.node.split(".")[0]) < 16) {
    throw new Error("需要 Node.js 16+ 和 npm。");
  }
  if (!["darwin", "linux", "win32"].includes(process.platform)
      || !["x64", "arm64"].includes(process.arch)) {
    throw new Error(`不支持的平台：${process.platform}-${process.arch}`);
  }
  const cacheDir = path.join(os.homedir(), ".cache", "pippit-tool-cli", "xyq-skill", `${process.platform}-${process.arch}`);
  const installedDir = path.join(cacheDir, "current");
  const binaryRelative = path.join("node_modules", "@pippit-dev", "cli", "bin", `pippit-tool-cli${process.platform === "win32" ? ".exe" : ""}`);
  const cachedCLI = path.join(installedDir, binaryRelative);
  const env = { ...process.env };
  delete env.XYQ_ACCESS_KEY;
  env.PIPPIT_CLI_DISABLE_UPDATE_CHECK = "1";
  let installDir;

  function run(command, args, label, quiet = false) {
    try {
      return execFileSync(command, args, {
        cwd: installDir, env, timeout: quiet ? 10000 : 660000,
        stdio: quiet ? ["ignore", "pipe", "pipe"] : ["ignore", 2, 2],
      });
    } catch (err) {
      const failure = new Error(`${label}失败（${err.code || err.status || "unknown"}），请检查安装、命令支持情况和网络。`);
      failure.exitStatus = err.status;
      throw failure;
    }
  }

  function checkCLI(cliPath, expectedVersion) {
    const version = run(cliPath, ["--version"], "检查 CLI 版本", true).toString().trim();
    if (expectedVersion && version !== expectedVersion) {
      throw new Error(`CLI 版本 ${version} 与 npm 包版本 ${expectedVersion} 不一致。`);
    }
    const commands = REQUIRED_COMMANDS.map((command) => command.split(" "));
    if (canvas) {
      commands.push(...REQUIRED_CANVAS_NATIVE_COMMANDS.map((command) => command.split(" ")));
    }
    for (const command of commands) {
      try {
        const help = run(cliPath, [...command, "--help"], `检查 ${command.join(" ")} 命令`, true).toString();
        if (canvas && command.join(" ") === "canvas get" && !/(?:^|\s)--team-id(?:[=\s,]|$)/m.test(help)) {
          const failure = new Error("CLI 缺少 --team-id 参数。");
          failure.missingCommand = "canvas get --team-id";
          throw failure;
        }
      } catch (err) {
        if (Number.isInteger(err.exitStatus) && err.exitStatus !== 0) {
          err.missingCommand = command.join(" ");
        }
        throw err;
      }
    }
    const result = { cli_path: cliPath, version };
    if (canvas) {
      // Semantic Canvas commands live in the npm package, not the Go binary.
      const entry = path.resolve(path.dirname(fs.realpathSync(cliPath)), "../scripts/run.js");
      try {
        if (!fs.existsSync(entry)) throw new Error("缺少 npm 入口");
        const catalog = JSON.parse(run(process.execPath, [entry, "canvas", "command", "list"], "检查 Canvas 运行时", true));
        if (!Array.isArray(catalog.commands) || !REQUIRED_CANVAS_COMMANDS.every(
          (name) => catalog.commands.some((command) => command.name === name),
        )) throw new Error("Canvas 命令目录不完整");
        const role = JSON.parse(run(process.execPath, [entry, "canvas", "command", "schema", "xyq.role.updateAppearance"], "检查角色提示词契约", true));
        if (!role.input_schema?.properties?.patch?.properties?.generationPrompt
            || !role.input_schema?.properties?.expected?.properties?.generationPrompt) {
          throw new Error("角色提示词独立字段契约不可用");
        }
        const generation = role.input_schema.properties.patch.properties.generation;
        const object = generation?.anyOf?.find((branch) => branch.type === "object");
        if (!object?.properties?.references || !role.input_schema.properties.expected.properties.generation) {
          throw new Error("角色派生图片生成配置与引用契约不可用");
        }
      } catch (err) {
        // Runtime timeouts are execution failures; do not repeatedly install to mask them.
        if (err.exitStatus === null) throw err;
        const failure = new Error("Canvas npm 入口或运行时不可用，请检查 CLI 安装。");
        failure.missingCommand = "canvas command runtime";
        throw failure;
      }
      result.canvas_entry = entry;
    }
    return result;
  }

  const candidates = new Set([findCLIOnPath(), fs.existsSync(cachedCLI) ? cachedCLI : null]);
  for (const candidate of candidates) {
    if (!candidate) continue;
    try {
      return checkCLI(candidate);
    } catch (err) {
      if (!err.missingCommand) throw err;
      console.error(`已有 CLI 的 ${err.missingCommand} 命令检查未通过；继续检查缓存，无可用缓存时自动安装最新版本。`);
    }
  }

  const [npm, npmArgs] = npmCommand();
  fs.mkdirSync(cacheDir, { recursive: true });
  installDir = fs.mkdtempSync(path.join(cacheDir, "install-"));
  try {
    run(npm, [...npmArgs, "install", "--prefix", installDir,
      "--cache", path.join(installDir, ".npm-cache"), "--global=false", "--no-save",
      "--package-lock=false", "--ignore-scripts", "--prefer-online",
      "--no-audit", "--no-fund", "@pippit-dev/cli@latest"], "下载最新 npm 包");
    const packageDir = path.join(installDir, "node_modules", "@pippit-dev", "cli");
    const installer = path.join(packageDir, "scripts", "install-cli.js");
    if (!fs.existsSync(installer)) {
      throw new Error("npm latest 尚未提供 scripts/install-cli.js，请先发布包含只安装 CLI 入口的版本。");
    }
    run(process.execPath, [installer], "下载并安装最新 CLI 二进制");
    const cliPath = path.join(packageDir, "bin", `pippit-tool-cli${process.platform === "win32" ? ".exe" : ""}`);
    const pkg = JSON.parse(fs.readFileSync(path.join(packageDir, "package.json"), "utf8"));
    const result = checkCLI(cliPath, pkg.version);
    // Preserve the previous installation until the replacement passes all checks.
    fs.rmSync(installedDir, { recursive: true, force: true });
    fs.renameSync(installDir, installedDir);
    return {
      ...result, cli_path: cachedCLI,
      ...(canvas ? { canvas_entry: fs.realpathSync(path.join(installedDir, "node_modules", "@pippit-dev", "cli", "scripts", "run.js")) } : {}),
    };
  } catch (err) {
    fs.rmSync(installDir, { recursive: true, force: true });
    throw err;
  }
}

if (require.main === module) {
  if (process.argv.length === 3 && process.argv[2] === "--help") {
    console.log("Usage: node ensure-cli.js [--canvas]\n优先复用 PATH 或缓存中命令齐全的 CLI，不存在或缺少必需命令时安装 npm latest，成功输出 {cli_path, version} JSON。--canvas 额外验证画布原生命令和 npm 运行时，并返回 canvas_entry。");
  } else if (process.argv.length !== 2 && !(process.argv.length === 3 && process.argv[2] === "--canvas")) {
    console.error("不支持的参数。用法：node ensure-cli.js [--canvas]");
    process.exitCode = 1;
  } else {
    try {
      console.log(JSON.stringify(ensureCLI({ canvas: process.argv[2] === "--canvas" })));
    } catch (err) {
      console.error(err.message);
      process.exitCode = 1;
    }
  }
}

module.exports = { ensureCLI };

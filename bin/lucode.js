#!/usr/bin/env node

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const appHome = path.resolve(__dirname, "..");
const isWindows = process.platform === "win32";

function addCandidate(candidates, command, source, requireExists = false) {
  if (!command) {
    return;
  }
  const value = String(command).trim();
  if (!value) {
    return;
  }
  if (requireExists && !fs.existsSync(value)) {
    return;
  }
  if (candidates.some((item) => item.command.toLowerCase() === value.toLowerCase())) {
    return;
  }
  candidates.push({ command: value, source });
}

function venvPython(root) {
  return path.join(root, isWindows ? "Scripts\\python.exe" : "bin/python");
}

function condaPython(prefix) {
  return path.join(prefix, isWindows ? "python.exe" : "bin/python");
}

function pythonCandidates() {
  const candidates = [];
  addCandidate(candidates, process.env.LUCODE_PYTHON, "LUCODE_PYTHON");
  addCandidate(candidates, process.env.LUCODE_VENV && venvPython(process.env.LUCODE_VENV), "LUCODE_VENV", true);
  addCandidate(candidates, process.env.CONDA_PREFIX && condaPython(process.env.CONDA_PREFIX), "CONDA_PREFIX", true);
  addCandidate(candidates, process.env.PYTHON, "PYTHON");
  const userHome = process.env.USERPROFILE || process.env.HOME || "";
  addCandidate(candidates, userHome && venvPython(path.join(userHome, ".lucode", "venv")), "~/.lucode/venv", true);
  addCandidate(candidates, venvPython(path.join(appHome, ".venv")), "app .venv", true);
  addCandidate(candidates, venvPython(path.join(process.cwd(), ".venv")), "workspace .venv", true);
  addCandidate(candidates, "python", "PATH");
  if (!isWindows) {
    addCandidate(candidates, "python3", "PATH");
  }
  return candidates;
}

function selectPython(candidates) {
  for (const candidate of candidates) {
    const probe = spawnSync(candidate.command, ["-c", "import sys"], {
      cwd: process.cwd(),
      env: process.env,
      stdio: "ignore",
    });
    if (!probe.error && probe.status === 0) {
      return candidate;
    }
  }
  return null;
}

const selectedPython = selectPython(pythonCandidates());
if (!selectedPython) {
  console.error("无法启动 Lucode：没有找到可用的 Python。");
  console.error("请安装 Python 3.11+，或设置 LUCODE_PYTHON 指向你的 conda/venv 解释器。");
  console.error("示例：set LUCODE_PYTHON=D:\\develop\\Data_anaconda2024\\envs\\agents-demo\\python.exe");
  process.exit(1);
}

const env = {
  ...process.env,
  PYTHONIOENCODING: process.env.PYTHONIOENCODING || "utf-8",
  PYTHONPATH: process.env.PYTHONPATH
    ? `${appHome}${path.delimiter}${process.env.PYTHONPATH}`
    : appHome,
  LUCODE_APP_HOME: process.env.LUCODE_APP_HOME || appHome,
  LUCODE_PYTHON: process.env.LUCODE_PYTHON || selectedPython.command,
  LUCODE_PYTHON_SOURCE: selectedPython.source,
};

const result = spawnSync(selectedPython.command, ["-m", "lucode", ...process.argv.slice(2)], {
  cwd: process.cwd(),
  env,
  stdio: "inherit",
});

if (result.error) {
  console.error(`无法启动 Lucode：${result.error.message}`);
  console.error("请检查 LUCODE_PYTHON 是否指向可执行的 Python，或运行 lucode doctor 查看诊断。");
  process.exit(1);
}

process.exit(result.status === null ? 1 : result.status);

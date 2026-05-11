#!/usr/bin/env node

const { spawnSync } = require("node:child_process");
const path = require("node:path");

const appHome = path.resolve(__dirname, "..");
const python = process.env.LUCODE_PYTHON || process.env.PYTHON || "python";
const env = {
  ...process.env,
  PYTHONIOENCODING: process.env.PYTHONIOENCODING || "utf-8",
  PYTHONPATH: process.env.PYTHONPATH
    ? `${appHome}${path.delimiter}${process.env.PYTHONPATH}`
    : appHome,
  LUCODE_APP_HOME: process.env.LUCODE_APP_HOME || appHome,
};

const result = spawnSync(python, ["-m", "lucode", ...process.argv.slice(2)], {
  cwd: process.cwd(),
  env,
  stdio: "inherit",
});

if (result.error) {
  console.error(`lucode failed to start: ${result.error.message}`);
  process.exit(1);
}

process.exit(result.status === null ? 1 : result.status);

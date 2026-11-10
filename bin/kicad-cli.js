#!/usr/bin/env node
"use strict";

// The launcher: runs the native program from the platform package npm installed for this
// machine. `kicad-cli update` points the command entry at the native program directly, which
// starts much faster; this launcher then stays as the fallback (DCLI-075).
const { spawn } = require("child_process");
const path = require("path");

const root = require("../package.json");
const tool = Object.keys(root.bin)[0];
const platformPackage = `${root.name}-${process.platform}-${process.arch}`;

if (!Object.prototype.hasOwnProperty.call(root.optionalDependencies || {}, platformPackage)) {
  process.stderr.write(`${tool} has no npm platform package for ${process.platform}-${process.arch}.\n`);
  process.exit(1);
}

let program;
try {
  const manifest = require.resolve(`${platformPackage}/package.json`);
  program = path.join(path.dirname(manifest), "bin", tool + (process.platform === "win32" ? ".exe" : ""));
} catch {
  process.stderr.write(`${tool}: the platform package ${platformPackage} is not installed.\n` +
    `Install again with: npm install -g ${root.name} --include=optional\n`);
  process.exit(1);
}

const child = spawn(program, process.argv.slice(2), { stdio: "inherit", windowsHide: true });

// A signal for the launcher is meant for the program: pass it on and wait, so the program can
// finish its write and report it. On Windows, Ctrl+C already reaches the program itself.
for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(signal, () => {
    if (process.platform !== "win32") {
      try {
        child.kill(signal);
      } catch {
        // the program has already ended
      }
    }
  });
}

child.on("error", (error) => {
  process.stderr.write(`${tool}: cannot start ${program}: ${error.message}\n`);
  process.exit(1);
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.removeAllListeners(signal);
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code === null ? 1 : code);
});

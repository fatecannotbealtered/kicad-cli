#!/usr/bin/env node
"use strict";

// Thin forwarder: exec the binary shipped by the npm platform package.
const { execFileSync } = require("child_process");
const path = require("path");

const rootPackage = require("../package.json");
const toolName = Object.keys(rootPackage.bin || {})[0] || "kicad-cli";
const ext = process.platform === "win32" ? ".exe" : "";
const platformKey = `${process.platform}-${process.arch}`;
const platformPackage = `${rootPackage.name}-${platformKey}`;
const optionalDependencies = rootPackage.optionalDependencies || {};

if (!Object.prototype.hasOwnProperty.call(optionalDependencies, platformPackage)) {
  console.error(
    `${toolName} does not ship an npm platform package for ${platformKey}.\n` +
    "Install a supported platform package or use the GitHub standalone binary."
  );
  process.exit(1);
}

// A published install resolves the platform package. A source checkout --
// which is what `npm link` from this repository produces -- has no platform
// package and never will, so it runs the source it was linked to. Without this
// the repository cannot be installed from itself: the command lands on PATH
// and every invocation reports a missing platform package.
//
// Order matters. The platform package wins when present, so a real install
// behaves exactly as before and a stray checkout beside it cannot shadow the
// artifact someone actually installed.
let bin = null;
try {
  const platformPackageJson = require.resolve(`${platformPackage}/package.json`);
  bin = path.join(path.dirname(platformPackageJson), "bin", toolName + ext);
} catch {
  bin = null;
}

if (bin === null) {
  const fs = require("fs");
  const root = path.join(__dirname, "..");
  if (!fs.existsSync(path.join(root, "kicad_cli", "main.py"))) {
    console.error(
      `${toolName} platform package ${platformPackage} is not installed.\n` +
      "This usually means npm optional dependencies were omitted.\n" +
      `Reinstall with:  npm install -g ${rootPackage.name} --include=optional`
    );
    process.exit(1);
  }
  // Run the checkout. Windows usually has `python`, POSIX usually has
  // `python3`; the other is tried after, and KICAD_CLI_NODE_PYTHON overrides
  // both for an interpreter that is neither.
  const candidates = process.env.KICAD_CLI_NODE_PYTHON
    ? [process.env.KICAD_CLI_NODE_PYTHON]
    : process.platform === "win32"
      ? ["python", "python3", "py"]
      : ["python3", "python"];
  for (const python of candidates) {
    try {
      execFileSync(python, ["-m", "kicad_cli.main", ...process.argv.slice(2)], {
        stdio: "inherit",
        cwd: root,
      });
      process.exit(0);
    } catch (e) {
      // ENOENT means this interpreter is not there; try the next. Anything
      // else is the tool's own exit status and must pass through, or every
      // failed command would look like a missing interpreter.
      if (e.code !== "ENOENT") process.exit(e.status || 1);
    }
  }
  console.error(
    `${toolName} is linked to the source checkout at ${root}, and no Python ` +
    "interpreter was found to run it.\n" +
    "Install Python 3.10+ or set KICAD_CLI_NODE_PYTHON to its path."
  );
  process.exit(1);
}

try {
  execFileSync(bin, process.argv.slice(2), { stdio: "inherit" });
} catch (e) {
  if (e.code === "ENOENT") {
    console.error(
      `${toolName} binary not found inside ${platformPackage}.\n` +
      `Reinstall with:  npm install -g ${rootPackage.name} --include=optional`
    );
  }
  process.exit(e.status || 1);
}

const fs = require("fs");
const path = require("path");
const { minify } = require("terser");

const ROOT = path.join(__dirname, "..");
const SRC_DIR = path.join(ROOT, "frontend", "js");
const OUT_DIR = path.join(ROOT, "static", "js");
const FILES = ["script.js", "watch.js", "m.js", "imagepreview.js", "dialog.js", "notice.js", "thumbnails.js"];

const OPTIONS = {
  ecma: 2020,
  compress: { passes: 2, toplevel: false, keep_fargs: true, drop_debugger: true },
  mangle: { toplevel: false },
  format: { comments: false, ascii_only: false },
  sourceMap: false,
};

function sleep(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

function replaceFile(tmpPath, outPath, content) {
  for (let i = 0; i < 10; i++) {
    try {
      fs.renameSync(tmpPath, outPath);
      return;
    } catch (e) {
      if (!["EPERM", "EBUSY", "EACCES"].includes(e.code)) throw e;
      sleep(300);
    }
  }
  fs.writeFileSync(outPath, content, "utf8");
  try { fs.unlinkSync(tmpPath); } catch (_) {}
}

async function main() {
  let failed = 0;
  for (const name of FILES) {
    const srcPath = path.join(SRC_DIR, name);
    if (!fs.existsSync(srcPath)) {
      console.warn(`[skip] ${srcPath} 없음`);
      continue;
    }
    const code = fs.readFileSync(srcPath, "utf8");
    try {
      const result = await minify({ [name]: code }, OPTIONS);
      const out = result.code + "\n";
      const outPath = path.join(OUT_DIR, name);
      fs.writeFileSync(outPath + ".tmp", out, "utf8");
      replaceFile(outPath + ".tmp", outPath, out);
      console.log(`[ok] ${name}  ${Buffer.byteLength(code)} -> ${Buffer.byteLength(out)} bytes`);
    } catch (e) {
      failed++;
      console.error(`[실패] ${name}: ${e.message}`);
    }
  }
  if (failed) process.exit(1);
}

main();
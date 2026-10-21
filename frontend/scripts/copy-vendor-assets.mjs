// Copies the browser assets that third-party packages would otherwise fetch from public CDNs into public/vendor,
// so the candidate room works offline and on-prem (runs before `next dev` and `next build`):
//   - MediaPipe Tasks Vision WebAssembly (camera integrity check, ADR 0005);
//   - Excalidraw fonts (whiteboard, ADR 0010).
// The face landmarker model itself is committed under public/models (see docs/DEPLOYMENT.md).

import { copyFile, mkdir, readdir, stat } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const modules = join(root, "node_modules");
const vendor = join(root, "public", "vendor");

const MEDIAPIPE_FILES = [
  "vision_wasm_internal.js",
  "vision_wasm_internal.wasm",
  "vision_wasm_nosimd_internal.js",
  "vision_wasm_nosimd_internal.wasm",
];

async function upToDate(source, target) {
  try {
    const [a, b] = await Promise.all([stat(source), stat(target)]);
    return a.size === b.size && a.mtimeMs <= b.mtimeMs;
  } catch {
    return false;
  }
}

async function copy(source, target) {
  if (await upToDate(source, target)) return 0;
  await mkdir(dirname(target), { recursive: true });
  await copyFile(source, target);
  return 1;
}

async function copyTree(source, target) {
  let copied = 0;
  for (const entry of await readdir(source, { withFileTypes: true })) {
    const from = join(source, entry.name);
    const to = join(target, entry.name);
    copied += entry.isDirectory() ? await copyTree(from, to) : await copy(from, to);
  }
  return copied;
}

let copied = 0;
for (const name of MEDIAPIPE_FILES) {
  copied += await copy(
    join(modules, "@mediapipe", "tasks-vision", "wasm", name),
    join(vendor, "mediapipe", "wasm", name),
  );
}
copied += await copyTree(
  join(modules, "@excalidraw", "excalidraw", "dist", "prod", "fonts"),
  join(vendor, "excalidraw", "fonts"),
);
console.log(`vendor assets: ${copied} file(s) copied to public/vendor`);

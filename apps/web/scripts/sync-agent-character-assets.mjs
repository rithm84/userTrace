import { createHash } from "node:crypto";
import { copyFile, mkdir, readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const CHARACTERS = ["coral", "black", "mint", "sky", "lilac", "peach", "aqua"];
const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(scriptDirectory, "../../..");
const webRoot = path.join(repositoryRoot, "apps/web");
const canonicalAssetDirectory = path.join(repositoryRoot, "packages/utrace-agent-character/assets");
const vendorWasmPath = createRequire(import.meta.url).resolve("@rive-app/webgl2/rive.wasm");
const publicAgentDirectory = path.join(webRoot, "public/agent");
const publicWasmPath = path.join(publicAgentDirectory, "rive.wasm");

const copies = CHARACTERS.map((character) => {
  const fileName = `utrace-agent-${character}.riv`;
  return {
    source: path.join(canonicalAssetDirectory, fileName),
    target: path.join(publicAgentDirectory, fileName),
  };
});
copies.push({ source: vendorWasmPath, target: publicWasmPath });

for (const { source } of copies.slice(0, CHARACTERS.length)) {
  const canonicalAsset = await readFile(source);
  if (canonicalAsset.subarray(0, 4).toString("ascii") !== "RIVE") {
    throw new Error(`Canonical agent character asset must begin with the ASCII RIVE header: ${source}`);
  }
}

await mkdir(publicAgentDirectory, { recursive: true });
await Promise.all(copies.map(({ source, target }) => copyFile(source, target)));

for (const { source, target } of copies) {
  const [sourceDigest, targetDigest] = await Promise.all([sha256(source), sha256(target)]);
  if (sourceDigest !== targetDigest) {
    throw new Error(
      `Agent character asset digest mismatch: source ${source} (${sourceDigest}), target ${target} (${targetDigest}).`,
    );
  }
  process.stdout.write(`${target} ${targetDigest}\n`);
}

// The landing page fetches each rig gzipped and inflates it in the browser: static hosts serve
// .riv as an opaque binary, uncompressed, and the rigs shrink about fivefold.
for (const { target } of copies.slice(0, CHARACTERS.length)) {
  const packed = `${target}.gz`;
  await writeFile(packed, gzipSync(await readFile(target), { level: 9 }));
  process.stdout.write(`${packed} ${await sha256(packed)}\n`);
}

async function sha256(filePath) {
  const bytes = await readFile(filePath);
  return createHash("sha256").update(bytes).digest("hex");
}

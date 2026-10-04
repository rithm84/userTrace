import { createRequire } from "node:module";
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { describe, expect, it } from "vitest";

const webRoot = process.cwd();
const repositoryRoot = path.resolve(webRoot, "../..");
const CHARACTERS = ["coral", "black", "mint", "sky", "lilac", "peach", "aqua"] as const;
const canonicalAssetDirectory = path.join(repositoryRoot, "packages/utrace-agent-character/assets");
const publicAgentDirectory = path.join(webRoot, "public/agent");
const publicWasmPath = path.join(publicAgentDirectory, "rive.wasm");
const vendorWasmPath = createRequire(import.meta.url).resolve("@rive-app/webgl2/rive.wasm");

describe("packaged agent character assets", () => {
  it.each(CHARACTERS)("copies the canonical %s Rive file byte-for-byte with a valid header", async (character) => {
    const fileName = `utrace-agent-${character}.riv`;
    const [canonicalAsset, publicAsset] = await Promise.all([
      readFile(path.join(canonicalAssetDirectory, fileName)),
      readFile(path.join(publicAgentDirectory, fileName)),
    ]);

    expect(canonicalAsset.subarray(0, 4).toString("ascii")).toBe("RIVE");
    expect(publicAsset).toEqual(canonicalAsset);
  });

  it("copies the installed runtime WASM byte-for-byte", async () => {
    const [vendorWasm, publicWasm] = await Promise.all([
      readFile(vendorWasmPath),
      readFile(publicWasmPath),
    ]);

    expect(publicWasm.equals(vendorWasm)).toBe(true);
  });

  it("does not package raster or SVG character artwork", async () => {
    const entries = await readdir(publicAgentDirectory);
    const imageExtensions = new Set([".png", ".jpg", ".jpeg", ".webp", ".svg"]);

    expect(entries.filter((entry) => imageExtensions.has(path.extname(entry).toLowerCase()))).toEqual(
      [],
    );
  });
});

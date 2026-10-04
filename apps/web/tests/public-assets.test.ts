import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const PUBLIC_DIR = join(process.cwd(), "public");
const IMAGE_EXTENSIONS = new Set([".svg", ".png", ".jpg", ".jpeg", ".webp"]);

function imageFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) {
      return imageFiles(path);
    }
    const lower = entry.toLowerCase();
    const isImage = [...IMAGE_EXTENSIONS].some((extension) => lower.endsWith(extension));
    return isImage ? [path] : [];
  });
}

describe("public image assets", () => {
  it("do not contain checked-in HTML/script dumps", () => {
    const offenders = imageFiles(PUBLIC_DIR).filter((path) => {
      const prefix = readFileSync(path, "utf8").slice(0, 512).toLowerCase();
      return prefix.includes("<!doctype html") || prefix.includes("<script");
    });

    expect(offenders).toEqual([]);
  });
});

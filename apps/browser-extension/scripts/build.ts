/**
 * Packages the extension.
 *
 * Four Vite passes produce the module service worker, the two extension pages,
 * and the two IIFE bundles Chrome injects into a page (content script and
 * main-world bridge). Nothing is fetched at runtime: the Rive JavaScript
 * runtime is bundled, and its WebAssembly, the character file, and the
 * generated icons are copied into the package.
 *
 * Usage: `node scripts/build.ts [--origins <path>] [--out <dir>]`.
 * Without flags it builds `config/build-origins.json` into `dist/`.
 */
import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { build, type InlineConfig } from "vite";

import { parseBuildOrigins, type BuildOrigins } from "../src/build/buildOrigins.ts";
import { createIconPng } from "../src/build/icon.ts";
import { createManifest, ICON_SIZES, MINIMUM_CHROME_VERSION } from "../src/build/manifest.ts";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

type BuildArguments = Readonly<{ originsPath: string; outDir: string }>;

/** Flags default to the committed production configuration and `dist/`. */
function parseArguments(argv: readonly string[]): BuildArguments {
  let originsPath = "config/build-origins.json";
  let outDir = "dist";

  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    const value = argv[index + 1];
    if (flag !== "--origins" && flag !== "--out") {
      throw new Error(`Unknown build flag ${String(flag)}; expected --origins or --out.`);
    }
    if (value === undefined) {
      throw new Error(`Build flag ${flag} requires a value.`);
    }
    if (flag === "--origins") {
      originsPath = value;
    } else {
      outDir = value;
    }
    index += 1;
  }

  return { originsPath: resolve(projectRoot, originsPath), outDir: resolve(projectRoot, outDir) };
}

async function readPackageVersion(): Promise<string> {
  const manifest: unknown = JSON.parse(
    await readFile(resolve(projectRoot, "package.json"), "utf8"),
  );
  if (
    manifest === null ||
    typeof manifest !== "object" ||
    !("version" in manifest) ||
    typeof manifest.version !== "string"
  ) {
    throw new Error("package.json is missing a string version.");
  }
  return manifest.version;
}

function sharedConfig(
  origins: BuildOrigins,
  version: string,
  outDir: string,
): InlineConfig {
  return {
    configFile: false,
    root: projectRoot,
    logLevel: "warn",
    define: {
      __UTRACE_BUILD_CONFIG__: JSON.stringify(origins),
      __UTRACE_EXTENSION_VERSION__: JSON.stringify(version),
    },
    build: {
      outDir,
      emptyOutDir: false,
      target: `chrome${MINIMUM_CHROME_VERSION}`,
      minify: false,
      sourcemap: false,
      modulePreload: false,
      reportCompressedSize: false,
    },
  };
}

function bundleConfig(
  base: InlineConfig,
  entry: string,
  fileName: string,
  format: "es" | "iife",
): InlineConfig {
  return {
    ...base,
    build: {
      ...base.build,
      rollupOptions: {
        input: resolve(projectRoot, entry),
        output: {
          format,
          entryFileNames: fileName,
          inlineDynamicImports: true,
          assetFileNames: "assets/[name][extname]",
        },
      },
    },
  };
}

async function writeIcons(outDir: string): Promise<void> {
  await mkdir(resolve(outDir, "icons"), { recursive: true });
  for (const size of ICON_SIZES) {
    await writeFile(resolve(outDir, `icons/icon-${size}.png`), createIconPng(size));
  }
}

async function main(): Promise<void> {
  const { originsPath, outDir } = parseArguments(process.argv.slice(2));
  const origins = parseBuildOrigins(JSON.parse(await readFile(originsPath, "utf8")));
  const version = await readPackageVersion();

  await rm(outDir, { recursive: true, force: true });
  await mkdir(outDir, { recursive: true });

  const base = sharedConfig(origins, version, outDir);
  await build(bundleConfig(base, "src/background/index.ts", "background.js", "es"));
  await build(bundleConfig(base, "src/content/index.ts", "content.js", "iife"));
  await build(
    bundleConfig(base, "src/mainworld/bridge.ts", "main-world-bridge.js", "iife"),
  );
  await build({
    ...base,
    root: resolve(projectRoot, "src/ui"),
    build: {
      ...base.build,
      rollupOptions: {
        input: {
          popup: resolve(projectRoot, "src/ui/popup.html"),
          options: resolve(projectRoot, "src/ui/options.html"),
        },
        output: { entryFileNames: "[name].js", assetFileNames: "assets/[name][extname]" },
      },
    },
  });

  await writeFile(
    resolve(outDir, "manifest.json"),
    `${JSON.stringify(createManifest(origins, version), null, 2)}\n`,
  );
  await writeIcons(outDir);
  await cp(
    resolve(projectRoot, "node_modules/@rive-app/canvas/rive.wasm"),
    resolve(outDir, "rive.wasm"),
  );
  await cp(
    resolve(
      projectRoot,
      "../../packages/utrace-agent-character/assets/utrace-agent-coral.riv",
    ),
    resolve(outDir, "agent-character.riv"),
  );

  process.stdout.write(`Packaged ${outDir} from ${originsPath}\n`);
}

await main();

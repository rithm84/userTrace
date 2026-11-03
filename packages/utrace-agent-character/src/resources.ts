import { RuntimeLoader } from "@rive-app/webgl2";

type AgentCharacterResourceErrorCode =
  | "invalid_resource_url"
  | "runtime_reconfigured";

export type AgentCharacterResources = Readonly<{
  assetUrl: string;
  wasmUrl: string;
}>;

export class AgentCharacterResourceError extends Error {
  readonly code: AgentCharacterResourceErrorCode;

  constructor(code: AgentCharacterResourceErrorCode, message: string) {
    super(message);
    this.name = "AgentCharacterResourceError";
    this.code = code;
  }
}

let configuredWasmUrl: string | null = null;

export function createAgentCharacterResources(
  assetUrl: string,
  wasmUrl: string,
): AgentCharacterResources {
  assertPackagedResourceUrl(assetUrl, "assetUrl");
  assertPackagedResourceUrl(wasmUrl, "wasmUrl");

  return Object.freeze({ assetUrl, wasmUrl });
}

export function configureAgentCharacterRuntime(
  resources: AgentCharacterResources,
): void {
  assertPackagedResourceUrl(resources.wasmUrl, "wasmUrl");

  if (configuredWasmUrl === resources.wasmUrl) {
    return;
  }

  if (configuredWasmUrl !== null) {
    throw new AgentCharacterResourceError(
      "runtime_reconfigured",
      `Rive runtime WASM is already configured as ${configuredWasmUrl} and cannot be reconfigured as ${resources.wasmUrl}.`,
    );
  }

  RuntimeLoader.setWasmFallbackUrl(null);
  RuntimeLoader.setWasmUrl(resources.wasmUrl);
  configuredWasmUrl = resources.wasmUrl;
}

function assertPackagedResourceUrl(url: string, name: string): void {
  if (typeof url === "string" && isRootRelativeUrl(url)) {
    return;
  }

  throw new AgentCharacterResourceError(
    "invalid_resource_url",
    `Agent character ${name} must be a root-relative path; received ${describeResourceUrl(url)}.`,
  );
}

function describeResourceUrl(url: string): string {
  return typeof url === "string" ? JSON.stringify(url) : `a value of type ${typeof url}`;
}

function isRootRelativeUrl(url: string): boolean {
  if (
    !url.startsWith("/") ||
    url.startsWith("//") ||
    /[\\\u0000-\u001F\u007F]/u.test(url)
  ) {
    return false;
  }

  const packagedOrigin = "https://packaged-resource.utrace.invalid";
  try {
    return new URL(url, packagedOrigin).origin === packagedOrigin;
  } catch {
    return false;
  }
}

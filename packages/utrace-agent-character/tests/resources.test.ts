import { beforeEach, describe, expect, it, vi } from "vitest";

const riveRuntime = vi.hoisted(() => ({
  setWasmUrl: vi.fn<(url: string) => void>(),
  setWasmFallbackUrl: vi.fn<(url: string | null) => void>(),
}));

vi.mock("@rive-app/webgl2", () => ({
  RuntimeLoader: {
    setWasmUrl: riveRuntime.setWasmUrl,
    setWasmFallbackUrl: riveRuntime.setWasmFallbackUrl,
  },
}));

describe("createAgentCharacterResources", () => {
  beforeEach(() => {
    riveRuntime.setWasmUrl.mockReset();
    riveRuntime.setWasmFallbackUrl.mockReset();
    vi.resetModules();
  });

  it.each([
    ["root-relative paths", "/agent/character.riv", "/agent/rive.wasm"],
    [
      "packaged extension URLs",
      "chrome-extension://abcdefghijklmnop/agent/character.riv",
      "chrome-extension://abcdefghijklmnop/agent/rive.wasm",
    ],
  ])("accepts %s without rewriting them", async (_description, assetUrl, wasmUrl) => {
    const { createAgentCharacterResources } = await import("../src/resources");

    const resources = createAgentCharacterResources(assetUrl, wasmUrl);

    expect(resources).toEqual({ assetUrl, wasmUrl });
    expect(Object.isFrozen(resources)).toBe(true);
  });

  it.each([
    "http://example.test/agent/character.riv",
    "https://example.test/agent/character.riv",
    "//example.test/agent/character.riv",
    "data:application/octet-stream;base64,AAAA",
    "blob:https://example.test/id",
  ])("rejects the unpackaged URL %s", async (invalidUrl) => {
    const { AgentCharacterResourceError, createAgentCharacterResources } = await import(
      "../src/resources"
    );

    expect(() => createAgentCharacterResources(invalidUrl, "/agent/rive.wasm")).toThrowError(
      AgentCharacterResourceError,
    );
    expect(() => createAgentCharacterResources("/agent/character.riv", invalidUrl)).toThrowError(
      expect.objectContaining({ code: "invalid_resource_url" }),
    );
  });

  it("rejects malformed extension URLs", async () => {
    const { createAgentCharacterResources } = await import("../src/resources");

    expect(() =>
      createAgentCharacterResources("chrome-extension:not-packaged", "/agent/rive.wasm"),
    ).toThrowError(expect.objectContaining({ code: "invalid_resource_url" }));
  });

  it.each([
    ["backslash network path", "/\\evil.example/rive.wasm"],
    ["tab-normalized network path", "/\t/evil.example/rive.wasm"],
    ["newline-normalized network path", "/\n/evil.example/rive.wasm"],
    ["carriage-return-normalized network path", "/\r/evil.example/rive.wasm"],
    ["NUL control character", "/\u0000/evil.example/rive.wasm"],
    ["DEL control character", "/\u007F/evil.example/rive.wasm"],
  ])("rejects a %s in either resource position", async (_description, invalidUrl) => {
    const { createAgentCharacterResources } = await import("../src/resources");

    expect(() => createAgentCharacterResources(invalidUrl, "/agent/rive.wasm")).toThrowError(
      expect.objectContaining({ code: "invalid_resource_url" }),
    );
    expect(() => createAgentCharacterResources("/agent/character.riv", invalidUrl)).toThrowError(
      expect.objectContaining({ code: "invalid_resource_url" }),
    );
  });

  it("rejects non-string resource values with the typed error", async () => {
    const { createAgentCharacterResources } = await import("../src/resources");

    expect(() =>
      createAgentCharacterResources(null as never, "/agent/rive.wasm"),
    ).toThrowError(expect.objectContaining({ code: "invalid_resource_url" }));
  });
});

describe("configureAgentCharacterRuntime", () => {
  beforeEach(() => {
    riveRuntime.setWasmUrl.mockReset();
    riveRuntime.setWasmFallbackUrl.mockReset();
    vi.resetModules();
  });

  it("configures the runtime once and treats the same URL as idempotent", async () => {
    const { configureAgentCharacterRuntime, createAgentCharacterResources } = await import(
      "../src/resources"
    );
    const resources = createAgentCharacterResources(
      "/agent/character.riv",
      "/agent/rive.wasm",
    );

    configureAgentCharacterRuntime(resources);
    configureAgentCharacterRuntime(resources);

    expect(riveRuntime.setWasmUrl).toHaveBeenCalledTimes(1);
    expect(riveRuntime.setWasmUrl).toHaveBeenCalledWith("/agent/rive.wasm");
    expect(riveRuntime.setWasmFallbackUrl).toHaveBeenCalledTimes(1);
    expect(riveRuntime.setWasmFallbackUrl).toHaveBeenCalledWith(null);
  });

  it("rejects a different runtime URL in the same module context", async () => {
    const {
      AgentCharacterResourceError,
      configureAgentCharacterRuntime,
      createAgentCharacterResources,
    } = await import("../src/resources");
    configureAgentCharacterRuntime(
      createAgentCharacterResources("/agent/character.riv", "/agent/rive.wasm"),
    );

    expect(() =>
      configureAgentCharacterRuntime(
        createAgentCharacterResources("/other/character.riv", "/other/rive.wasm"),
      ),
    ).toThrowError(AgentCharacterResourceError);
    expect(() =>
      configureAgentCharacterRuntime(
        createAgentCharacterResources("/other/character.riv", "/other/rive.wasm"),
      ),
    ).toThrowError(expect.objectContaining({ code: "runtime_reconfigured" }));
    expect(riveRuntime.setWasmUrl).toHaveBeenCalledTimes(1);
    expect(riveRuntime.setWasmFallbackUrl).toHaveBeenCalledTimes(1);
  });
});

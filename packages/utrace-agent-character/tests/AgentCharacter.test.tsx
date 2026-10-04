import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const riveDouble = vi.hoisted(() => {
  const setterOrder: string[] = [];
  const numberSetters = {
    gazeX: vi.fn<(value: number) => void>(() => setterOrder.push("gazeX")),
    gazeY: vi.fn<(value: number) => void>(() => setterOrder.push("gazeY")),
    speechEnergy: vi.fn<(value: number) => void>(() => setterOrder.push("speechEnergy")),
    motionScale: vi.fn<(value: number) => void>(() => setterOrder.push("motionScale")),
  };
  const enumSetter = vi.fn<(value: string) => void>(() => setterOrder.push("state"));

  return {
    cleanup: vi.fn<() => void>(),
    enumSetter,
    enumValues: [
      "idle",
      "investigating",
      "listening",
      "thinking",
      "speaking",
      "acknowledgement",
      "concern",
      "building",
      "completion",
      "ship",
      "reconnecting",
      "paused",
      "sleep",
      "pointer",
    ],
    enumAccessorCalls: 0,
    loadErrorCallback: null as RendererParameters["onLoadError"] | null,
    missingBindings: new Set<string>(),
    numberAccessorCalls: 0,
    numberSetters,
    params: null as RendererParameters | null,
    setWasmFallbackUrl: vi.fn<(url: string | null) => void>(),
    setWasmUrl: vi.fn<(url: string) => void>(),
    setterOrder,
    usedPaths: [] as string[],
  };
});

type RendererEvent = Readonly<{
  data?: string;
  type: string;
}>;

type RendererParameters = Readonly<{
  alignment?: string;
  artboard: string;
  autoBind: boolean;
  autoplay: boolean;
  enableRiveAssetCDN: boolean;
  layout: Readonly<{ alignment: string; fit: string }>;
  onLoadError: (event: RendererEvent) => void;
  src: string;
  stateMachines: string;
}>;

vi.mock("@rive-app/webgl2", () => ({
  Alignment: { Center: "center" },
  Fit: { Contain: "contain" },
  Layout: class Layout {
    readonly alignment: string;
    readonly fit: string;

    constructor(parameters: Readonly<{ alignment: string; fit: string }>) {
      this.alignment = parameters.alignment;
      this.fit = parameters.fit;
    }
  },
  RuntimeLoader: {
    setWasmFallbackUrl: riveDouble.setWasmFallbackUrl,
    setWasmUrl: riveDouble.setWasmUrl,
  },
}));

vi.mock("@rive-app/react-webgl2", async () => {
  const React = await import("react");
  const fakeViewModelInstance = {
    enum: (path: string) => {
      riveDouble.enumAccessorCalls += 1;
      return path === "state" && !riveDouble.missingBindings.has(path)
        ? { values: riveDouble.enumValues }
        : null;
    },
    number: (path: string) => {
      riveDouble.numberAccessorCalls += 1;
      return path in riveDouble.numberSetters && !riveDouble.missingBindings.has(path)
        ? { value: 0 }
        : null;
    },
  };

  return {
    useRive: (parameters: RendererParameters) => {
      riveDouble.params = parameters;
      riveDouble.loadErrorCallback ??= parameters.onLoadError;
      React.useEffect(() => () => riveDouble.cleanup(), []);
      return {
        rive: { viewModelInstance: fakeViewModelInstance },
        RiveComponent: (props: React.ComponentProps<"canvas">) => (
          <canvas data-testid="rive-canvas" {...props} />
        ),
      };
    },
    useViewModelInstanceEnum: (path: string) => {
      riveDouble.usedPaths.push(path);
      return {
        setValue: riveDouble.enumSetter,
        value: "idle",
        values: riveDouble.enumValues,
      };
    },
    useViewModelInstanceNumber: (path: keyof typeof riveDouble.numberSetters) => {
      riveDouble.usedPaths.push(path);
      return { setValue: riveDouble.numberSetters[path], value: 0 };
    },
  };
});

import {
  CHARACTER_STATES,
  CharacterProjectionError,
  acceptCharacterProjection,
  createAgentCharacterResources,
} from "../src";
import { AgentCharacter, AgentCharacterError } from "../src/AgentCharacter";

const resources = createAgentCharacterResources(
  "/agent/utrace-agent-coral.riv",
  "/agent/rive.wasm",
);

function acceptedProjection(
  revision: number,
  state: (typeof CHARACTER_STATES)[number],
  gazeX: number,
  gazeY: number,
  speechEnergy: number,
  motionScale: number,
) {
  const decision = acceptCharacterProjection(null, {
    gazeX,
    gazeY,
    motionScale,
    revision,
    speechEnergy,
    state,
  });
  if (decision.kind !== "applied") {
    throw new Error("A valid initial projection was not accepted.");
  }
  return decision.projection;
}

const initialProjection = acceptedProjection(7, "speaking", -0.4, 0.3, 0.62, 1);

describe("AgentCharacter", () => {
  beforeEach(() => {
    riveDouble.cleanup.mockReset();
    riveDouble.enumSetter.mockClear();
    riveDouble.enumAccessorCalls = 0;
    riveDouble.enumValues.splice(0, riveDouble.enumValues.length, ...CHARACTER_STATES);
    riveDouble.missingBindings.clear();
    riveDouble.loadErrorCallback = null;
    riveDouble.numberAccessorCalls = 0;
    Object.values(riveDouble.numberSetters).forEach((setter) => setter.mockClear());
    riveDouble.params = null;
    riveDouble.setterOrder.splice(0);
    riveDouble.usedPaths.splice(0);
  });

  afterEach(() => cleanup());

  it("loads the one packaged artboard and state machine with contain-center layout", async () => {
    render(
      <AgentCharacter
        dataTestId="agent"
        onError={vi.fn()}
        projection={initialProjection}
        resources={resources}
        size={236}
      />,
    );

    await waitFor(() => expect(riveDouble.params).not.toBeNull());
    expect(riveDouble.params).toMatchObject({
      artboard: "AgentCharacter",
      autoBind: true,
      autoplay: true,
      enableRiveAssetCDN: false,
      layout: { alignment: "center", fit: "contain" },
      src: "/agent/utrace-agent-coral.riv",
      stateMachines: "AgentCharacterMachine",
    });
  });

  it("renders the explicit diagnostic wrapper and a presentation-only canvas", async () => {
    const { container } = render(
      <AgentCharacter
        dataTestId="agent"
        onError={vi.fn()}
        projection={initialProjection}
        resources={resources}
        size={56}
      />,
    );

    const wrapper = await screen.findByTestId("agent");
    expect(wrapper.style.height).toBe("56px");
    expect(wrapper.style.width).toBe("56px");
    expect(wrapper.getAttribute("data-state")).toBe("speaking");
    expect(wrapper.getAttribute("data-revision")).toBe("7");
    expect(wrapper.getAttribute("data-motion-scale")).toBe("1");
    expect((await screen.findByTestId("rive-canvas")).getAttribute("aria-hidden")).toBe("true");
    expect(container.querySelector("img, video")).toBeNull();
    expect(wrapper.textContent).toBe("");
  });

  it("applies every numeric property before the semantic state", async () => {
    render(
      <AgentCharacter
        dataTestId="agent"
        onError={vi.fn()}
        projection={initialProjection}
        resources={resources}
        size={236}
      />,
    );

    await waitFor(() =>
      expect(riveDouble.setterOrder).toEqual([
        "gazeX",
        "gazeY",
        "speechEnergy",
        "motionScale",
        "state",
      ]),
    );
    expect(riveDouble.numberSetters.gazeX).toHaveBeenCalledWith(-0.4);
    expect(riveDouble.numberSetters.gazeY).toHaveBeenCalledWith(0.3);
    expect(riveDouble.numberSetters.speechEnergy).toHaveBeenCalledWith(0.62);
    expect(riveDouble.numberSetters.motionScale).toHaveBeenCalledWith(1);
    expect(riveDouble.enumSetter).toHaveBeenCalledWith("speaking");
  });

  it("skips stale and duplicate projections", async () => {
    const onError = vi.fn();
    const { rerender } = render(
      <AgentCharacter
        dataTestId="agent"
        onError={onError}
        projection={initialProjection}
        resources={resources}
        size={236}
      />,
    );
    await waitFor(() => expect(riveDouble.enumSetter).toHaveBeenCalledTimes(1));
    riveDouble.setterOrder.splice(0);
    Object.values(riveDouble.numberSetters).forEach((setter) => setter.mockClear());
    riveDouble.enumSetter.mockClear();

    const stale = acceptedProjection(6, "idle", 0, 0, 0, 1);
    rerender(
      <AgentCharacter
        dataTestId="agent"
        onError={onError}
        projection={stale}
        resources={resources}
        size={236}
      />,
    );
    rerender(
      <AgentCharacter
        dataTestId="agent"
        onError={onError}
        projection={initialProjection}
        resources={resources}
        size={236}
      />,
    );

    await act(async () => undefined);
    expect(riveDouble.setterOrder).toEqual([]);
    expect(onError).not.toHaveBeenCalled();
  });

  it("reports same-revision conflicts without changing Rive values", async () => {
    const onError = vi.fn();
    const { rerender } = render(
      <AgentCharacter
        dataTestId="agent"
        onError={onError}
        projection={initialProjection}
        resources={resources}
        size={236}
      />,
    );
    await waitFor(() => expect(riveDouble.enumSetter).toHaveBeenCalledTimes(1));
    riveDouble.setterOrder.splice(0);

    rerender(
      <AgentCharacter
        dataTestId="agent"
        onError={onError}
        projection={acceptedProjection(7, "speaking", 0.2, 0.3, 0.62, 1)}
        resources={resources}
        size={236}
      />,
    );

    await waitFor(() => expect(onError).toHaveBeenCalledTimes(1));
    expect(onError.mock.calls[0]?.[0]).toBeInstanceOf(CharacterProjectionError);
    expect(riveDouble.setterOrder).toEqual([]);
  });

  it.each(["state", "gazeX", "gazeY", "speechEnergy", "motionScale"])(
    "requires the %s binding",
    async (missingPath) => {
      riveDouble.missingBindings.add(missingPath);
      const onError = vi.fn();
      render(
        <AgentCharacter
          dataTestId="agent"
          onError={onError}
          projection={initialProjection}
          resources={resources}
          size={236}
        />,
      );

      await waitFor(() => expect(onError).toHaveBeenCalledTimes(1));
      expect(onError.mock.calls[0]?.[0]).toEqual(
        expect.objectContaining({ code: "binding_contract_mismatch" }),
      );
      expect(riveDouble.setterOrder).toEqual([]);
    },
  );

  it.each([
    ["missing option", CHARACTER_STATES.slice(0, -1)],
    ["extra option", [...CHARACTER_STATES, "unexpected"]],
    ["duplicate option", [...CHARACTER_STATES.slice(0, -1), "idle"]],
  ])("rejects %s in the state enum", async (_description, enumValues) => {
    riveDouble.enumValues.splice(0, riveDouble.enumValues.length, ...enumValues);
    const onError = vi.fn();
    render(
      <AgentCharacter
        dataTestId="agent"
        onError={onError}
        projection={initialProjection}
        resources={resources}
        size={236}
      />,
    );

    await waitFor(() => expect(onError).toHaveBeenCalledTimes(1));
    expect(onError.mock.calls[0]?.[0]).toBeInstanceOf(AgentCharacterError);
    expect(riveDouble.setterOrder).toEqual([]);
  });

  it("accepts the Rive runtime enum order when it contains the exact semantic state set", async () => {
    riveDouble.enumValues.reverse();
    const onError = vi.fn();
    render(
      <AgentCharacter
        dataTestId="agent"
        onError={onError}
        projection={initialProjection}
        resources={resources}
        size={236}
      />,
    );

    await waitFor(() => expect(riveDouble.enumSetter).toHaveBeenCalledTimes(1));
    expect(onError).not.toHaveBeenCalled();
    expect(riveDouble.setterOrder).toEqual([
      "gazeX",
      "gazeY",
      "speechEnergy",
      "motionScale",
      "state",
    ]);
  });

  it("reports runtime load failures", async () => {
    const onError = vi.fn();
    render(
      <AgentCharacter
        dataTestId="agent"
        onError={onError}
        projection={initialProjection}
        resources={resources}
        size={236}
      />,
    );
    await waitFor(() => expect(riveDouble.params).not.toBeNull());

    act(() => riveDouble.loadErrorCallback?.({ data: "bad bytes", type: "loaderror" }));

    expect(onError).toHaveBeenCalledWith(
      expect.objectContaining({ code: "asset_load_failed" }),
    );
  });

  it("reports a delayed load failure through the latest error handler", async () => {
    const firstOnError = vi.fn();
    const latestOnError = vi.fn();
    const view = render(
      <AgentCharacter
        dataTestId="agent"
        onError={firstOnError}
        projection={initialProjection}
        resources={resources}
        size={236}
      />,
    );
    await waitFor(() => expect(riveDouble.loadErrorCallback).not.toBeNull());

    view.rerender(
      <AgentCharacter
        dataTestId="agent"
        onError={latestOnError}
        projection={initialProjection}
        resources={resources}
        size={236}
      />,
    );
    act(() => riveDouble.loadErrorCallback?.({ data: "late failure", type: "loaderror" }));

    expect(firstOnError).not.toHaveBeenCalled();
    expect(latestOnError).toHaveBeenCalledWith(
      expect.objectContaining({ code: "asset_load_failed" }),
    );
  });

  it("validates a successful binding contract once per view-model instance", async () => {
    const view = render(
      <AgentCharacter
        dataTestId="agent"
        onError={vi.fn()}
        projection={initialProjection}
        resources={resources}
        size={236}
      />,
    );
    await waitFor(() => expect(riveDouble.enumSetter).toHaveBeenCalledTimes(1));
    expect(riveDouble.numberAccessorCalls).toBe(4);
    expect(riveDouble.enumAccessorCalls).toBe(1);

    view.rerender(
      <AgentCharacter
        dataTestId="agent"
        onError={vi.fn()}
        projection={acceptedProjection(8, "listening", 0, 0, 0, 0.5)}
        resources={resources}
        size={236}
      />,
    );
    await waitFor(() => expect(riveDouble.enumSetter).toHaveBeenCalledTimes(2));

    expect(riveDouble.numberAccessorCalls).toBe(4);
    expect(riveDouble.enumAccessorCalls).toBe(1);
  });

  it("allows the Rive hook lifecycle to clean up on unmount", async () => {
    const view = render(
      <AgentCharacter
        dataTestId="agent"
        onError={vi.fn()}
        projection={initialProjection}
        resources={resources}
        size={236}
      />,
    );
    await screen.findByTestId("rive-canvas");

    view.unmount();

    expect(riveDouble.cleanup).toHaveBeenCalledTimes(1);
  });

  it("rejects non-positive and non-finite sizes", () => {
    for (const size of [0, -1, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(() =>
        render(
          <AgentCharacter
            dataTestId="agent"
            onError={vi.fn()}
            projection={initialProjection}
            resources={resources}
            size={size}
          />,
        ),
      ).toThrowError(RangeError);
    }
  });

  it("uses every required data-binding path", async () => {
    render(
      <AgentCharacter
        dataTestId="agent"
        onError={vi.fn()}
        projection={initialProjection}
        resources={resources}
        size={236}
      />,
    );

    await waitFor(() =>
      expect(new Set(riveDouble.usedPaths)).toEqual(
        new Set(["state", "gazeX", "gazeY", "speechEnergy", "motionScale"]),
      ),
    );
  });
});

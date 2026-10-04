import {
  useRive,
  useViewModelInstanceEnum,
  useViewModelInstanceNumber,
} from "@rive-app/react-webgl2";
import {
  Alignment,
  Fit,
  Layout,
  type Event as RiveEvent,
  type ViewModelInstance,
} from "@rive-app/webgl2";
import { useCallback, useEffect, useRef, useState, type JSX } from "react";

import {
  CHARACTER_STATES,
  CharacterProjectionError,
  acceptCharacterProjection,
  type AcceptedCharacterProjection,
} from "./contracts";
import {
  configureAgentCharacterRuntime,
  type AgentCharacterResources,
} from "./resources";

export type AgentCharacterErrorCode =
  | "asset_load_failed"
  | "binding_contract_mismatch";

export class AgentCharacterError extends Error {
  readonly code: AgentCharacterErrorCode;

  constructor(code: AgentCharacterErrorCode, message: string) {
    super(message);
    this.name = "AgentCharacterError";
    this.code = code;
  }
}

export type AgentCharacterFailure =
  | AgentCharacterError
  | CharacterProjectionError;

export type AgentCharacterProps = Readonly<{
  dataTestId: string;
  onError: (error: AgentCharacterFailure) => void;
  projection: AcceptedCharacterProjection;
  resources: AgentCharacterResources;
  size: number;
}>;

type LoadedAgentCharacterProps = Readonly<{
  onError: AgentCharacterProps["onError"];
  projection: AcceptedCharacterProjection;
  resources: AgentCharacterResources;
}>;

const NUMBER_BINDING_PATHS = [
  "gazeX",
  "gazeY",
  "speechEnergy",
  "motionScale",
] as const;

export function AgentCharacter(props: AgentCharacterProps): JSX.Element {
  assertValidSize(props.size);
  const [runtimeConfigured, setRuntimeConfigured] = useState(false);

  useEffect(() => {
    configureAgentCharacterRuntime(props.resources);
    setRuntimeConfigured(true);
  }, [props.resources]);

  return (
    <div
      data-motion-scale={props.projection.motionScale}
      data-revision={props.projection.revision}
      data-state={props.projection.state}
      data-testid={props.dataTestId}
      style={{ height: props.size, width: props.size }}
    >
      {runtimeConfigured ? (
        <LoadedAgentCharacter
          onError={props.onError}
          projection={props.projection}
          resources={props.resources}
        />
      ) : null}
    </div>
  );
}

function LoadedAgentCharacter(props: LoadedAgentCharacterProps): JSX.Element {
  const onError = useRef(props.onError);
  onError.current = props.onError;
  const handleLoadError = useCallback((event: RiveEvent): void => {
    onError.current(
      new AgentCharacterError("asset_load_failed", String(event)),
    );
  }, []);
  const { rive, RiveComponent } = useRive({
    src: props.resources.assetUrl,
    artboard: "AgentCharacter",
    stateMachines: "AgentCharacterMachine",
    autoplay: true,
    autoBind: true,
    enableRiveAssetCDN: false,
    layout: new Layout({ fit: Fit.Contain, alignment: Alignment.Center }),
    onLoadError: handleLoadError,
  });
  const viewModelInstance = rive?.viewModelInstance ?? null;
  const state = useViewModelInstanceEnum("state", viewModelInstance);
  const gazeX = useViewModelInstanceNumber("gazeX", viewModelInstance);
  const gazeY = useViewModelInstanceNumber("gazeY", viewModelInstance);
  const speechEnergy = useViewModelInstanceNumber("speechEnergy", viewModelInstance);
  const motionScale = useViewModelInstanceNumber("motionScale", viewModelInstance);
  const lastApplied = useRef<AcceptedCharacterProjection | null>(null);
  const lastViewModelInstance = useRef<ViewModelInstance | null>(null);
  const reportedBindingError = useRef<string | null>(null);
  const validatedViewModelInstance = useRef<ViewModelInstance | null>(null);

  useEffect(() => {
    if (viewModelInstance === null) {
      return;
    }

    if (lastViewModelInstance.current !== viewModelInstance) {
      lastViewModelInstance.current = viewModelInstance;
      lastApplied.current = null;
      reportedBindingError.current = null;
      validatedViewModelInstance.current = null;
    }

    if (validatedViewModelInstance.current !== viewModelInstance) {
      const contractError = findBindingContractError(viewModelInstance);
      if (contractError !== null) {
        if (reportedBindingError.current !== contractError) {
          reportedBindingError.current = contractError;
          onError.current(
            new AgentCharacterError("binding_contract_mismatch", contractError),
          );
        }
        return;
      }
      validatedViewModelInstance.current = viewModelInstance;
      reportedBindingError.current = null;
    }

    try {
      const decision = acceptCharacterProjection(
        lastApplied.current,
        props.projection,
      );
      if (decision.kind !== "applied") {
        return;
      }

      gazeX.setValue(decision.projection.gazeX);
      gazeY.setValue(decision.projection.gazeY);
      speechEnergy.setValue(decision.projection.speechEnergy);
      motionScale.setValue(decision.projection.motionScale);
      state.setValue(decision.projection.state);
      lastApplied.current = decision.projection;
    } catch (error) {
      if (error instanceof CharacterProjectionError) {
        onError.current(error);
        return;
      }
      throw error;
    }
  }, [
    gazeX.setValue,
    gazeY.setValue,
    motionScale.setValue,
    props.projection,
    speechEnergy.setValue,
    state.setValue,
    viewModelInstance,
  ]);

  return (
    <RiveComponent
      aria-hidden="true"
      style={{ height: "100%", width: "100%" }}
    />
  );
}

function assertValidSize(size: number): void {
  if (!Number.isFinite(size) || size <= 0) {
    throw new RangeError(
      `Agent character size must be a positive finite number; received ${String(size)}.`,
    );
  }
}

function findBindingContractError(
  viewModelInstance: ViewModelInstance,
): string | null {
  for (const path of NUMBER_BINDING_PATHS) {
    if (viewModelInstance.number(path) === null) {
      return `AgentCharacterModel is missing the required number binding ${path}.`;
    }
  }

  const state = viewModelInstance.enum("state");
  if (state === null) {
    return "AgentCharacterModel is missing the required enum binding state.";
  }
  if (!hasExactStateOptions(state.values)) {
    return `AgentCharacterModel state options must exactly equal ${CHARACTER_STATES.join(", ")}.`;
  }

  return null;
}

function hasExactStateOptions(options: readonly string[]): boolean {
  return (
    options.length === CHARACTER_STATES.length &&
    CHARACTER_STATES.every((expected) => options.includes(expected))
  );
}

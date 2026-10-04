/**
 * The instrumented child frame of the test application.
 *
 * It authenticates its own document and joins the top-document coordinator. It
 * obtains no telemetry credential and never exports.
 */

import { activateUTraceFrame, type UTraceFrameAttachment } from "../../../src/index.js";

interface FrameTestSurface {
  readonly ready: Promise<void>;
  readonly attachment: () => { readonly runtimeId: string; readonly frameId: string; readonly attached: boolean };
  readonly error: () => string | null;
}

declare global {
  interface Window {
    __utraceFrameTest?: FrameTestSurface;
  }
}

const start = (): void => {
  const button = document.getElementById("frame-action");
  if (button !== null) {
    button.addEventListener("click", () => {
      const note = document.createElement("li");
      note.setAttribute("aria-label", "Frame row");
      note.textContent = "Frame row";
      document.getElementById("frame-list")?.append(note);
    });
  }

  let attachment: UTraceFrameAttachment | null = null;
  let error: string | null = null;
  const ready = activateUTraceFrame(window, {
    sdkInstallationId: "shopalytics-preview",
    environmentName: "preview",
    clientReleaseId: "shopalytics-main-test",
    schemaVersion: "1",
    protocolVersion: "1",
  }).then(
    (value) => {
      attachment = value;
    },
    (cause: unknown) => {
      error = cause instanceof Error ? cause.message : String(cause);
      throw cause;
    },
  );

  window.__utraceFrameTest = {
    ready,
    attachment: (): { runtimeId: string; frameId: string; attached: boolean } => {
      if (attachment === null) throw new Error("the frame has not attached");
      return { runtimeId: attachment.runtimeId, frameId: attachment.frameId, attached: attachment.attached() };
    },
    error: (): string | null => error,
  };
};

start();

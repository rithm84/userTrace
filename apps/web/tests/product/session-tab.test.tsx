import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SessionTab } from "@/components/session/SessionTab";
import type { SessionProjection } from "@/lib/api/schemas";
import { fixtureSessionProjection } from "@/lib/api/fixture-data";

const actions = vi.hoisted(() => ({
  refreshSessionProjection: vi.fn(),
  sendSessionControl: vi.fn(),
  openPreviewHandoff: vi.fn(),
  openVoiceConnection: vi.fn(),
  fetchBindingOffer: vi.fn(),
  submitSessionPreflight: vi.fn(),
}));
const realtime = vi.hoisted(() => ({ subscribeToSession: vi.fn() }));
const voice = vi.hoisted(() => ({ connectVoice: vi.fn() }));
const extension = vi.hoisted(() => ({ deliverBindingOffer: vi.fn(), probeExtension: vi.fn() }));

vi.mock("@/lib/session/actions", () => actions);
vi.mock("@/lib/session/realtime", () => ({
  ...realtime,
  SESSION_PROJECTION_TABLE: "session_projections",
}));
vi.mock("@/lib/session/voice", async () => {
  const actual = await vi.importActual<typeof import("@/lib/session/voice")>("@/lib/session/voice");
  return { ...actual, connectVoice: voice.connectVoice };
});
vi.mock("@/lib/session/extension", async () => {
  const actual = await vi.importActual<typeof import("@/lib/session/extension")>(
    "@/lib/session/extension",
  );
  return { ...actual, ...extension };
});

const EXTENSION_ID = "abcdefghijklmnopabcdefghijklmnop";

function projectionWith(overrides: Partial<SessionProjection>): SessionProjection {
  return { ...fixtureSessionProjection, ...overrides };
}

function renderSession(projection: SessionProjection) {
  return render(<SessionTab initialProjection={projection} extensionId={EXTENSION_ID} voiceTransport="webrtc" />);
}

beforeEach(() => {
  realtime.subscribeToSession.mockImplementation(
    (parameters: { onStatus: (status: string) => void }) => {
      parameters.onStatus("live");
      return () => undefined;
    },
  );
  voice.connectVoice.mockResolvedValue({
    connectionId: "conn-1",
    voiceRunId: "vr-1",
    setMicrophoneEnabled: vi.fn(),
    close: vi.fn(),
  });
  extension.deliverBindingOffer.mockResolvedValue(undefined);
  actions.fetchBindingOffer.mockResolvedValue({
    ok: true,
    value: {
      schema_version: 1,
      offer_token: "opaque",
      expires_at: "2026-10-03T09:05:00Z",
      protocol_version: "1.0",
      session_id: fixtureSessionProjection.session_id,
      client_id: "0199a0c0-0000-7aaa-8000-00000000c101",
      extension_installation_id: "0199a0c0-0000-7aaa-8000-000000001457",
      preview_runtime_allocation_id: "0199a0c0-0000-7aaa-8000-00000000a110",
    },
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("session tab", () => {
  it("shows the durable projection: character state, status text and controls", async () => {
    renderSession(fixtureSessionProjection);

    const character = screen.getByTestId("session-character");
    expect(character).toHaveAttribute("data-state", "listening");
    expect(character).toHaveAttribute("data-revision", "5");
    expect(screen.getByTestId("session-status")).toHaveTextContent("Listening");
    expect(screen.getByTestId("session-tab")).toHaveAttribute("data-revision", "9");
    expect(screen.getByTestId("session-control-mute")).toHaveTextContent("Mute");

    await waitFor(() => {
      expect(screen.getByTestId("session-connection")).toHaveTextContent("Connected");
    });
  });

  it("opens the microphone only once the backend sideband is listening", async () => {
    const microphone = vi.fn();
    voice.connectVoice.mockResolvedValue({
      connectionId: "conn-1",
      voiceRunId: "vr-1",
      setMicrophoneEnabled: microphone,
      close: vi.fn(),
    });
    let publish: (next: SessionProjection) => void = () => undefined;
    realtime.subscribeToSession.mockImplementation(
      (parameters: {
        onStatus: (status: string) => void;
        onProjection: (next: SessionProjection) => void;
      }) => {
        parameters.onStatus("live");
        publish = parameters.onProjection;
        return () => undefined;
      },
    );
    const waiting = projectionWith({ readiness: { conversational: false, observation: true } });
    renderSession(waiting);

    await waitFor(() => {
      expect(microphone).toHaveBeenLastCalledWith(false);
    });
    expect(screen.getByTestId("session-connection")).toHaveTextContent("Connecting");

    act(() => {
      publish({
        ...waiting,
        revision: waiting.revision + 1,
        readiness: { conversational: true, observation: true },
      });
    });
    await waitFor(() => {
      expect(microphone).toHaveBeenLastCalledWith(true);
    });
    expect(screen.getByTestId("session-connection")).toHaveTextContent("Connected");
  });

  it("delivers the binding offer to the extension id the deployment configures", async () => {
    renderSession(fixtureSessionProjection);

    await waitFor(() => {
      expect(extension.deliverBindingOffer).toHaveBeenCalledWith(
        EXTENSION_ID,
        fixtureSessionProjection.session_id,
        expect.objectContaining({ offer_token: "opaque" }),
      );
    });
  });

  it("applies the revision the control plane returns rather than assuming the press worked", async () => {
    actions.sendSessionControl.mockResolvedValue({
      ok: true,
      value: {
        revision: 13,
        state: "interviewing",
        controls: { microphone: "muted", available: ["unmute", "pause", "finish_later", "end_confirm"] },
      },
    });
    renderSession(fixtureSessionProjection);

    await userEvent.click(screen.getByTestId("session-control-mute"));

    await waitFor(() => {
      expect(screen.getByTestId("session-tab")).toHaveAttribute("data-revision", "13");
    });
    expect(screen.getByTestId("session-controls")).toHaveAttribute("data-microphone", "muted");
    expect(screen.getByTestId("session-control-mute")).toHaveTextContent("Unmute");
    expect(actions.sendSessionControl).toHaveBeenCalledWith(fixtureSessionProjection.session_id, "mute");
  });

  it("keeps the recorded state when a control is refused", async () => {
    actions.sendSessionControl.mockResolvedValue({ ok: false, message: "That session moved on." });
    renderSession(fixtureSessionProjection);

    await userEvent.click(screen.getByTestId("session-control-pause"));

    await waitFor(() => {
      expect(screen.getByTestId("session-notice")).toHaveTextContent("That session moved on.");
    });
    expect(screen.getByTestId("session-tab")).toHaveAttribute("data-revision", "9");
    expect(screen.getByTestId("session-tab")).toHaveAttribute("data-state", "interviewing");
  });

  it("asks for confirmation before ending the interview", async () => {
    actions.sendSessionControl.mockResolvedValue({
      ok: true,
      value: { revision: 20, state: "ended", controls: { microphone: "unmuted", available: [] } },
    });
    renderSession(fixtureSessionProjection);

    await userEvent.click(screen.getByTestId("session-control-end"));
    expect(actions.sendSessionControl).not.toHaveBeenCalled();
    expect(screen.getByTestId("session-end-warning")).toBeInTheDocument();

    await userEvent.click(screen.getByTestId("session-control-end-confirm"));
    expect(actions.sendSessionControl).toHaveBeenCalledWith(fixtureSessionProjection.session_id, "end_confirm");
    await waitFor(() => {
      expect(screen.getByTestId("session-ended")).toBeInTheDocument();
    });
  });

  it("streams what each side says into the transcript, one turn per speaker", async () => {
    voice.connectVoice.mockImplementation(
      async (parameters: { onTranscriptDelta: (delta: object) => void }) => {
        parameters.onTranscriptDelta({ speaker: "agent", text: "Walk me", startMs: 0, endMs: 300 });
        parameters.onTranscriptDelta({ speaker: "agent", text: " through Monday.", startMs: 300, endMs: 900 });
        parameters.onTranscriptDelta({ speaker: "participant", text: " Sure.", startMs: 1200, endMs: 1500 });
        return { connectionId: "conn-1", voiceRunId: "vr-1", setMicrophoneEnabled: vi.fn(), close: vi.fn() };
      },
    );
    renderSession(fixtureSessionProjection);

    await waitFor(() => {
      expect(screen.getByTestId("session-transcript-turn-1")).toHaveAttribute("data-speaker", "participant");
    });
    expect(screen.getByTestId("session-transcript-turn-0")).toHaveTextContent("Ivo");
    expect(screen.getByTestId("session-transcript-turn-0")).toHaveTextContent("Walk me through Monday.");
    expect(screen.getByTestId("session-transcript-turn-1")).toHaveTextContent("YouSure.");
  });

  it("marks the implementation progress state the control plane reports", () => {
    renderSession(projectionWith({ implementation_progress: "preparing_the_preview" }));

    const trail = screen.getByTestId("implementation-progress");
    expect(trail).toHaveAttribute("data-progress", "preparing_the_preview");
    expect(screen.getByTestId("progress-making_changes")).toHaveAttribute("data-reached", "true");
    expect(screen.getByTestId("progress-preparing_the_preview")).toHaveAttribute("data-current", "true");
    expect(screen.getByTestId("progress-ready_for_validation")).toHaveAttribute("data-reached", "false");
  });

  it("opens the preview in a new tab through a one-time front-door handoff", async () => {
    const replace = vi.fn();
    const previewTab = { opener: {}, location: { replace }, close: vi.fn() } as unknown as Window;
    const open = vi.spyOn(window, "open").mockReturnValue(previewTab);
    actions.openPreviewHandoff.mockResolvedValue({
      ok: true,
      value: {
        front_door_url: "https://opaque.preview.utrace.dev/?handoff=1",
        expires_at: "2026-10-03T09:05:00Z",
      },
    });
    renderSession(fixtureSessionProjection);

    await userEvent.click(screen.getByTestId("session-open-preview"));

    expect(open).toHaveBeenCalledWith("about:blank", "_blank");
    await waitFor(() => {
      expect(replace).toHaveBeenCalledWith("https://opaque.preview.utrace.dev/?handoff=1");
    });
    open.mockRestore();
  });

  it("says so when live updates are unavailable instead of inventing state", async () => {
    realtime.subscribeToSession.mockImplementation(
      (parameters: { onStatus: (status: string) => void }) => {
        parameters.onStatus("unavailable");
        return () => undefined;
      },
    );
    renderSession(fixtureSessionProjection);

    await waitFor(() => {
      expect(screen.getByTestId("session-realtime-status")).toHaveTextContent(
        "shows the last state uTrace recorded",
      );
    });
  });

  it("explains an unavailable observation path without ending the conversation", () => {
    renderSession(
      projectionWith({
        readiness: {
          conversational: true,
          observation: false,
          unavailable_detail: "I can't see the product right now.",
        },
      }),
    );

    expect(screen.getByTestId("session-readiness")).toHaveTextContent("I can't see the product right now.");
    expect(screen.getByTestId("session-controls")).toBeInTheDocument();
  });

  it("runs the preflight before interviewing and refreshes once it passes", async () => {
    actions.submitSessionPreflight.mockResolvedValue({
      ok: true,
      value: {
        passed: true,
        revision: 1,
        checks: [{ id: "supported_browser", status: "passed", detail: "Google Chrome 141" }],
      },
    });
    actions.refreshSessionProjection.mockResolvedValue({
      ok: true,
      value: fixtureSessionProjection,
    });
    extension.probeExtension.mockResolvedValue({
      type: "utrace_preflight_result",
      account_linked: true,
      host_permission: true,
      installation_proof: "proof",
      protocol_version: "1",
    });
    vi.stubGlobal("navigator", {
      userAgent: "Mozilla/5.0 Chrome/141.0.0.0 Safari/537.36",
      mediaDevices: {
        getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: vi.fn() }] }),
      },
    });

    renderSession(projectionWith({ state: "participant_setup", phase: "preflight" }));
    expect(screen.getByTestId("preflight-panel")).toBeInTheDocument();

    await userEvent.click(screen.getByTestId("preflight-run"));

    await waitFor(() => {
      expect(actions.submitSessionPreflight).toHaveBeenCalledWith(
        fixtureSessionProjection.session_id,
        expect.objectContaining({
          browser: expect.objectContaining({ supported: true }),
          extension: expect.objectContaining({ installed: true, account_linked: true }),
          permissions: { host: true, microphone: true },
        }),
      );
    });
    expect(actions.refreshSessionProjection).toHaveBeenCalledWith(
      fixtureSessionProjection.session_id,
    );
    await waitFor(() => {
      expect(screen.getByTestId("session-tab")).toHaveAttribute("data-state", "interviewing");
    });
    vi.unstubAllGlobals();
  });

  it("explains the ended state and offers the way back to uTrace", async () => {
    renderSession(projectionWith({ state: "ended", end_reason: "finish_later" }));

    expect(screen.getByTestId("session-ended")).toHaveAttribute("data-reason", "finish_later");
    expect(screen.getByTestId("session-ended-reason")).toHaveTextContent("private link uTrace sends you");

    await userEvent.click(screen.getByTestId("session-return"));
    expect(screen.getByTestId("session-returned")).toBeInTheDocument();
    expect(screen.getByTestId("session-open-settings")).toHaveAttribute("href", "/user-settings");
  });
});

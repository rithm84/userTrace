import type { PipecatClientOptions, RTVIEventCallbacks } from "@pipecat-ai/client-js";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  TranscriptEventError,
  appendTranscriptDelta,
  readBotOutput,
  readUserTranscription,
  type ShownSegments,
  type TranscriptDelta,
  type TranscriptTurn,
} from "@/lib/session/transcript";
import { VoiceError, connectVoice, type ConnectVoiceParameters } from "@/lib/session/voice";

const pipecat = vi.hoisted(() => ({
  clients: [] as Array<{
    options: PipecatClientOptions;
    connect: ReturnType<typeof vi.fn>;
    disconnect: ReturnType<typeof vi.fn>;
    enableMic: ReturnType<typeof vi.fn>;
  }>,
  transports: [] as unknown[],
  connect: null as null | (() => Promise<unknown>),
}));

vi.mock("@pipecat-ai/client-js", async () => {
  const actual = await vi.importActual<typeof import("@pipecat-ai/client-js")>("@pipecat-ai/client-js");
  class PipecatClient {
    readonly options: PipecatClientOptions;
    readonly connect = vi.fn(() => (pipecat.connect ?? (async () => ({ version: "2.1.0" })))());
    readonly disconnect = vi.fn(async () => undefined);
    readonly enableMic = vi.fn();
    constructor(options: PipecatClientOptions) {
      this.options = options;
      pipecat.clients.push(this);
    }
  }
  return { ...actual, PipecatClient };
});
vi.mock("@pipecat-ai/daily-transport", () => ({
  DailyTransport: class DailyTransport {
    readonly options: unknown;
    constructor(options: unknown) {
      this.options = options;
      pipecat.transports.push(options);
    }
  },
}));

const RECORD = {
  room_url: "https://utrace.daily.co/room-1",
  participant_token: "token-1",
  connection_id: "0199a0c0-0000-7aaa-8000-000000000101",
  voice_run_id: "0199a0c0-0000-7aaa-8000-000000000102",
};

function parametersWith(overrides: Partial<ConnectVoiceParameters>): ConnectVoiceParameters {
  return {
    openConnection: vi.fn().mockResolvedValue(RECORD),
    onAgentAudio: vi.fn(),
    onDisconnected: vi.fn(),
    onTranscriptDelta: vi.fn(),
    onNotice: vi.fn(),
    agentReadyTimeoutMs: 1000,
    ...overrides,
  };
}

function callbacks(): RTVIEventCallbacks {
  const client = pipecat.clients.at(-1);
  if (client?.options.callbacks === undefined) throw new Error("No Pipecat client was created.");
  return client.options.callbacks;
}

afterEach(() => {
  pipecat.clients.length = 0;
  pipecat.transports.length = 0;
  pipecat.connect = null;
  vi.useRealTimers();
});

describe("session voice connection", () => {
  it("joins the API's Daily room with its token, microphone off and echo cancellation on", async () => {
    const parameters = parametersWith({});

    const session = await connectVoice(parameters);

    expect(parameters.openConnection).toHaveBeenCalledTimes(1);
    const [client] = pipecat.clients;
    expect(client?.connect).toHaveBeenCalledWith({ url: RECORD.room_url, token: RECORD.participant_token });
    expect(client?.options).toMatchObject({ enableMic: false, enableCam: false });
    expect(pipecat.transports[0]).toMatchObject({
      dailyConfig: { avoidEval: true },
      inputSettings: {
        audio: { settings: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } },
      },
    });
    expect(session.connectionId).toBe(RECORD.connection_id);
    expect(session.voiceRunId).toBe(RECORD.voice_run_id);

    session.setMicrophoneEnabled(true);
    expect(client?.enableMic).toHaveBeenLastCalledWith(true);

    session.close();
    expect(client?.disconnect).toHaveBeenCalledTimes(1);
    callbacks().onDisconnected?.();
    expect(parameters.onDisconnected).not.toHaveBeenCalled();
  });

  it("plays only the agent's audio track and reports a call that drops", async () => {
    const parameters = parametersWith({});
    await connectVoice(parameters);
    const agentAudio = { kind: "audio" } as MediaStreamTrack;

    callbacks().onTrackStarted?.({ kind: "audio" } as MediaStreamTrack, { id: "me", name: "Maya", local: true });
    callbacks().onTrackStarted?.(agentAudio, { id: "bot", name: "Ivo", local: false });
    callbacks().onDisconnected?.();

    expect(parameters.onAgentAudio).toHaveBeenCalledTimes(1);
    expect(parameters.onAgentAudio).toHaveBeenCalledWith(agentAudio);
    expect(parameters.onDisconnected).toHaveBeenCalledTimes(1);
  });

  it("feeds final user transcripts and spoken agent output into the transcript", async () => {
    const parameters = parametersWith({});
    await connectVoice(parameters);
    const progress = (segment_id: number, accumulated_text: string, remaining_text: string) => ({
      text: accumulated_text + remaining_text,
      segment_id,
      spoken_progress: { accumulated_text, remaining_text },
    });

    callbacks().onBotOutput?.({ text: "Hi Maya.", segment_id: 1, spoken_status: "new", spoken_progress: { accumulated_text: "", remaining_text: "Hi Maya." } });
    callbacks().onBotOutput?.(progress(1, "Hi", " Maya."));
    callbacks().onBotOutput?.(progress(1, "Hi Maya.", ""));
    callbacks().onUserTranscript?.({ text: "Hi th", final: false, timestamp: "t", user_id: "u" });
    callbacks().onUserTranscript?.({ text: "Hi there.", final: true, timestamp: "t", user_id: "u" });
    callbacks().onBotOutput?.({ text: "bad" });
    callbacks().onBotOutput?.({ text: "x", spoken_progress: { accumulated_text: "x", remaining_text: "" } });

    expect(vi.mocked(parameters.onTranscriptDelta).mock.calls).toEqual([
      [{ speaker: "agent", text: " Hi" }],
      [{ speaker: "agent", text: " Maya." }],
      [{ speaker: "participant", text: " Hi there." }],
    ]);
    expect(parameters.onNotice).toHaveBeenCalledTimes(1);
    expect(vi.mocked(parameters.onNotice).mock.calls[0]?.[0]).toContain("segment_id");
  });

  it("leaves the call and fails loudly when the agent never becomes ready", async () => {
    vi.useFakeTimers();
    pipecat.connect = () => new Promise(() => undefined);

    const attempt = connectVoice(parametersWith({ agentReadyTimeoutMs: 50 }));
    const settled = expect(attempt).rejects.toMatchObject({ name: "VoiceError", code: "agent_unavailable" });
    await vi.advanceTimersByTimeAsync(50);

    await settled;
    expect(pipecat.clients[0]?.disconnect).toHaveBeenCalledTimes(1);
  });

  it("does not join a call when the API refuses the connection", async () => {
    const refused = new VoiceError("join_failed", "That session moved on.", {});

    await expect(
      connectVoice(parametersWith({ openConnection: vi.fn().mockRejectedValue(refused) })),
    ).rejects.toBe(refused);
    expect(pipecat.clients).toHaveLength(0);
  });
});

describe("session transcript", () => {
  it("removes gesture marks from what the agent says", () => {
    const read = readBotOutput(new Map(), {
      text: 'Look at <c id="T5"/>this filter.',
      segment_id: 3,
      spoken_progress: { accumulated_text: 'Look at <c id="T5"/>this filter.', remaining_text: "" },
    });

    expect(read.delta).toEqual({ speaker: "agent", text: " Look at this filter." });
  });

  it("refuses spoken text that rewrites words already shown", () => {
    const shown: ShownSegments = new Map([[1, "Hello there"]]);

    expect(() =>
      readBotOutput(shown, { text: "Goodbye", segment_id: 1, spoken_progress: { accumulated_text: "Goodbye", remaining_text: "" } }),
    ).toThrow(TranscriptEventError);
    expect(() => readUserTranscription({ text: "no final flag" })).toThrow(TranscriptEventError);
  });

  it("joins consecutive fragments from one speaker into a turn", () => {
    const deltas: TranscriptDelta[] = [
      { speaker: "agent", text: " Walk me" },
      { speaker: "agent", text: " through Monday." },
      { speaker: "participant", text: " Sure." },
    ];
    const turns = deltas.reduce<readonly TranscriptTurn[]>(appendTranscriptDelta, []);

    expect(turns.map((turn) => [turn.speaker, turn.text])).toEqual([
      ["agent", "Walk me through Monday."],
      ["participant", "Sure."],
    ]);
  });
});

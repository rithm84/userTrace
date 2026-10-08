"use client";

import { PipecatClient, RTVIError, type RTVIMessage } from "@pipecat-ai/client-js";
import { DailyTransport } from "@pipecat-ai/daily-transport";
import { z } from "zod";

import type { VoiceConnectionRecord } from "@/lib/api/schemas";

import {
  TranscriptEventError,
  readBotOutput,
  readUserTranscription,
  type ShownSegments,
  type TranscriptDelta,
} from "./transcript";

/**
 * The session tab's voice connection.
 *
 * The uTrace API opens the connection and returns a Daily room and a meeting
 * token for this participant; the voice agent, a Pipecat pipeline on the uTrace
 * worker, joins the same room. This tab joins through Pipecat's client with its
 * Daily transport and waits for the agent's RTVI `bot-ready`. No model, voice or
 * provider credential is selected here. Over the call the tab receives the
 * agent's audio and the RTVI transcript events that feed the live transcript.
 * The microphone starts disabled; the tab enables it through
 * `setMicrophoneEnabled`.
 */

export type VoiceErrorCode = "join_failed" | "agent_unavailable";

export class VoiceError extends Error {
  readonly code: VoiceErrorCode;

  constructor(code: VoiceErrorCode, message: string, options: { cause?: unknown }) {
    super(message, options);
    this.name = "VoiceError";
    this.code = code;
  }
}

export type VoiceSession = Readonly<{
  connectionId: string;
  voiceRunId: string;
  setMicrophoneEnabled: (enabled: boolean) => void;
  close: () => void;
}>;

export type ConnectVoiceParameters = Readonly<{
  openConnection: () => Promise<VoiceConnectionRecord>;
  onAgentAudio: (track: MediaStreamTrack) => void;
  onDisconnected: () => void;
  onTranscriptDelta: (delta: TranscriptDelta) => void;
  onNotice: (message: string) => void;
  agentReadyTimeoutMs: number;
}>;

/** The browser's own echo cancellation keeps the agent's voice out of the microphone. */
const MICROPHONE_CONSTRAINTS: MediaTrackConstraints = {
  echoCancellation: true,
  noiseSuppression: true,
  autoGainControl: true,
};

const rtviErrorSchema = z.object({ error: z.string(), fatal: z.boolean() });

export async function connectVoice(parameters: ConnectVoiceParameters): Promise<VoiceSession> {
  const record = await parameters.openConnection();
  let closed = false;
  let shown: ShownSegments = new Map();

  const readEvent = (read: () => TranscriptDelta | null): void => {
    try {
      const delta = read();
      if (delta !== null) {
        parameters.onTranscriptDelta(delta);
      }
    } catch (error) {
      if (error instanceof TranscriptEventError) {
        parameters.onNotice(error.message);
        return;
      }
      throw error;
    }
  };

  const client = new PipecatClient({
    // `avoidEval` loads Daily's call bundle with a script tag, which the
    // Content-Security-Policy admits, instead of evaluating fetched code.
    transport: new DailyTransport({
      dailyConfig: { avoidEval: true },
      inputSettings: { audio: { settings: MICROPHONE_CONSTRAINTS } },
    }),
    enableMic: false,
    enableCam: false,
    callbacks: {
      onTrackStarted: (track, participant) => {
        if (participant !== undefined && !participant.local && track.kind === "audio") {
          parameters.onAgentAudio(track);
        }
      },
      onUserTranscript: (data) => readEvent(() => readUserTranscription(data)),
      onBotOutput: (data) =>
        readEvent(() => {
          const read = readBotOutput(shown, data);
          shown = read.shown;
          return read.delta;
        }),
      // The camera stays off, so the microphone is the only device that can fail.
      onDeviceError: (error) => {
        parameters.onNotice(
          `uTrace could not use your microphone (${error.type}). Allow microphone access for this tab and try again.`,
        );
      },
      onError: (message: RTVIMessage) => {
        const data = rtviErrorSchema.safeParse(message.data);
        parameters.onNotice(
          data.success
            ? `The voice call reported an error: ${data.data.error}`
            : `The voice call reported an error the client could not read: ${JSON.stringify(message.data)}`,
        );
      },
      onDisconnected: () => {
        if (!closed) {
          parameters.onDisconnected();
        }
      },
    },
  });

  const close = (): void => {
    if (closed) return;
    closed = true;
    client.disconnect().catch((error: unknown) => {
      parameters.onNotice(
        `uTrace could not leave the voice call cleanly: ${error instanceof Error ? error.message : String(error)}`,
      );
    });
  };

  try {
    await withTimeout(
      client.connect({ url: record.room_url, token: record.participant_token }),
      parameters.agentReadyTimeoutMs,
    );
  } catch (error) {
    close();
    if (error instanceof AgentTimeoutError) {
      throw new VoiceError(
        "agent_unavailable",
        `The interview agent did not join the call within ${String(parameters.agentReadyTimeoutMs)} ms.`,
        { cause: error },
      );
    }
    if (error instanceof RTVIError) {
      throw new VoiceError("join_failed", `uTrace could not join the voice call: ${error.message}`, {
        cause: error,
      });
    }
    throw error;
  }

  return {
    connectionId: record.connection_id,
    voiceRunId: record.voice_run_id,
    setMicrophoneEnabled: (enabled: boolean): void => {
      client.enableMic(enabled);
    },
    close,
  };
}

class AgentTimeoutError extends Error {
  constructor(timeoutMs: number) {
    super(`No bot-ready within ${String(timeoutMs)} ms.`);
    this.name = "AgentTimeoutError";
  }
}

/** `connect` resolves only on the agent's `bot-ready`, so an agent that never joins fails here. */
function withTimeout<Value>(work: Promise<Value>, timeoutMs: number): Promise<Value> {
  return new Promise<Value>((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new AgentTimeoutError(timeoutMs)), timeoutMs);
    work.then(
      (value) => {
        window.clearTimeout(timer);
        resolve(value);
      },
      (error: unknown) => {
        window.clearTimeout(timer);
        reject(error);
      },
    );
  });
}

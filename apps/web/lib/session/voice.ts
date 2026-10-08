"use client";

import { PipecatClient, type RTVIMessage } from "@pipecat-ai/client-js";
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
 * token for this participant, the same room again when the session's agent is
 * still holding its call; the voice agent, a Pipecat pipeline on the uTrace
 * worker, joins that room. The connection is up once this tab has joined the
 * room: the agent arrives on its own schedule, and the session's readiness, not
 * this tab, says when it is in the call. No model, voice or provider credential
 * is selected here. Over the call the tab receives the agent's audio and the
 * RTVI transcript events that feed the live transcript. The microphone starts
 * disabled; the tab enables it through `setMicrophoneEnabled`.
 */

export type VoiceErrorCode = "join_failed";

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
  joinTimeoutMs: number;
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

  // `connect` settles only once the agent is ready, which is the agent's schedule,
  // not this tab's: the call is up once the tab has joined the room.
  let joined: () => void = () => undefined;
  let failed: (error: unknown) => void = () => undefined;
  const joining = new Promise<void>((resolve, reject) => {
    joined = resolve;
    failed = reject;
  });

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
      onConnected: () => joined(),
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

  client.connect({ url: record.room_url, token: record.participant_token }).catch((error: unknown) => {
    failed(error);
    if (!closed) {
      parameters.onDisconnected();
    }
  });
  try {
    await withTimeout(joining, parameters.joinTimeoutMs);
  } catch (error) {
    close();
    throw new VoiceError(
      "join_failed",
      error instanceof JoinTimeoutError
        ? `uTrace could not join the voice call within ${String(parameters.joinTimeoutMs)} ms.`
        : `uTrace could not join the voice call: ${error instanceof Error ? error.message : String(error)}`,
      { cause: error },
    );
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

class JoinTimeoutError extends Error {
  constructor(timeoutMs: number) {
    super(`Not joined within ${String(timeoutMs)} ms.`);
    this.name = "JoinTimeoutError";
  }
}

function withTimeout<Value>(work: Promise<Value>, timeoutMs: number): Promise<Value> {
  return new Promise<Value>((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new JoinTimeoutError(timeoutMs)), timeoutMs);
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

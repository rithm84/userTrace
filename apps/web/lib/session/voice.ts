"use client";

import type { VoiceConnectionRecord } from "@/lib/api/schemas";

import { TranscriptEventError, readTranscriptMessage, type TranscriptDelta } from "./transcript";

/**
 * The session tab's voice connection.
 *
 * The browser captures the microphone, offers one audio track and the
 * `oai-events` data channel (created before the offer, as GPT-Live's WebRTC
 * guide requires), and posts the SDP offer to the uTrace API. The API creates
 * the GPT-Live session on the OpenAI API, choosing the model, voice,
 * instructions and delegation server-side, and returns the answer; media then
 * flows directly between this browser and OpenAI. No provider endpoint, model
 * or credential is ever selected here. The session starts with the API's
 * request, so the tab never sends `session.start`, and the session's
 * data-channel permissions let it send no command at all: it only receives the
 * transcript fragments that feed the tab's live transcript. The microphone track
 * starts disabled; the tab enables it through `setMicrophoneEnabled`.
 */

export type VoiceErrorCode =
  | "microphone_unavailable"
  | "negotiation_failed"
  | "ice_timeout"
  | "answer_rejected";

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
  exchangeOffer: (sdpOffer: string) => Promise<VoiceConnectionRecord>;
  onRemoteStream: (stream: MediaStream) => void;
  onConnectionStateChange: (state: RTCPeerConnectionState) => void;
  onTranscriptDelta: (delta: TranscriptDelta) => void;
  onEventRejected: (message: string) => void;
  iceGatheringTimeoutMs: number;
}>;

export async function connectVoice(parameters: ConnectVoiceParameters): Promise<VoiceSession> {
  const media = navigator.mediaDevices;
  if (media === undefined) {
    throw new VoiceError(
      "microphone_unavailable",
      "This browser exposes no microphone, so the interview cannot start.",
      {},
    );
  }

  let localStream: MediaStream;
  try {
    localStream = await media.getUserMedia({ audio: true });
  } catch (error) {
    throw new VoiceError(
      "microphone_unavailable",
      "uTrace could not use your microphone. Allow microphone access for this tab and try again.",
      { cause: error },
    );
  }
  for (const track of localStream.getAudioTracks()) {
    track.enabled = false;
  }

  const peerConnection = new RTCPeerConnection();
  const dataChannel = peerConnection.createDataChannel("oai-events");
  dataChannel.onmessage = (event: MessageEvent<unknown>): void => {
    if (typeof event.data !== "string") {
      parameters.onEventRejected("The voice data channel sent a binary message; uTrace expects JSON events.");
      return;
    }
    try {
      const delta = readTranscriptMessage(event.data);
      if (delta !== null) {
        parameters.onTranscriptDelta(delta);
      }
    } catch (error) {
      if (error instanceof TranscriptEventError) {
        parameters.onEventRejected(error.message);
        return;
      }
      throw error;
    }
  };
  let closed = false;

  const close = (): void => {
    if (closed) return;
    closed = true;
    dataChannel.close();
    peerConnection.close();
    for (const track of localStream.getTracks()) {
      track.stop();
    }
  };

  try {
    for (const track of localStream.getAudioTracks()) {
      peerConnection.addTrack(track, localStream);
    }
    peerConnection.ontrack = (event: RTCTrackEvent): void => {
      const [stream] = event.streams;
      if (stream !== undefined) {
        parameters.onRemoteStream(stream);
      }
    };
    peerConnection.onconnectionstatechange = (): void => {
      parameters.onConnectionStateChange(peerConnection.connectionState);
    };

    const offer = await peerConnection.createOffer();
    await peerConnection.setLocalDescription(offer);
    await waitForIceGathering(peerConnection, parameters.iceGatheringTimeoutMs);

    const localDescription = peerConnection.localDescription;
    if (localDescription === null) {
      throw new VoiceError("negotiation_failed", "The browser produced no session description.", {});
    }

    const record = await parameters.exchangeOffer(localDescription.sdp);
    try {
      await peerConnection.setRemoteDescription({ type: "answer", sdp: record.sdp_answer });
    } catch (error) {
      throw new VoiceError(
        "answer_rejected",
        "The browser refused the session description the uTrace API returned.",
        { cause: error },
      );
    }

    return {
      connectionId: record.connection_id,
      voiceRunId: record.voice_run_id,
      setMicrophoneEnabled: (enabled: boolean): void => {
        for (const track of localStream.getAudioTracks()) {
          track.enabled = enabled;
        }
      },
      close,
    };
  } catch (error) {
    close();
    throw error;
  }
}

/**
 * One SDP exchange carries every candidate, so gathering must finish before the
 * offer is posted. A connection that never finishes gathering fails here rather
 * than sending an offer the provider cannot answer.
 */
function waitForIceGathering(peerConnection: RTCPeerConnection, timeoutMs: number): Promise<void> {
  if (peerConnection.iceGatheringState === "complete") {
    return Promise.resolve();
  }

  return new Promise<void>((resolve, reject) => {
    const settle = (): void => {
      peerConnection.removeEventListener("icegatheringstatechange", onChange);
      window.clearTimeout(timer);
    };
    const onChange = (): void => {
      if (peerConnection.iceGatheringState === "complete") {
        settle();
        resolve();
      }
    };
    const timer = window.setTimeout(() => {
      settle();
      reject(
        new VoiceError(
          "ice_timeout",
          `The browser did not finish gathering connection candidates within ${String(timeoutMs)} ms.`,
          {},
        ),
      );
    }, timeoutMs);
    peerConnection.addEventListener("icegatheringstatechange", onChange);
  });
}

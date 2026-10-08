import { afterEach, describe, expect, it, vi } from "vitest";

import { VoiceError, connectVoice } from "@/lib/session/voice";

const OFFER_SDP = "v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\n";
const ANSWER_SDP = "v=0\r\no=- 2 2 IN IP4 127.0.0.1\r\n";

class FakePeerConnection {
  iceGatheringState: RTCIceGatheringState = "complete";
  connectionState: RTCPeerConnectionState = "new";
  localDescription: RTCSessionDescriptionInit | null = null;
  remoteDescription: RTCSessionDescriptionInit | null = null;
  readonly added: MediaStreamTrack[] = [];
  readonly channels: string[] = [];
  readonly dataChannel: { close: () => void; onmessage: ((event: { data: unknown }) => void) | null } = {
    close: () => undefined,
    onmessage: null,
  };
  closed = false;
  ontrack: ((event: RTCTrackEvent) => void) | null = null;
  onconnectionstatechange: (() => void) | null = null;

  createDataChannel(label: string) {
    this.channels.push(label);
    return this.dataChannel;
  }

  addTrack(track: MediaStreamTrack) {
    this.added.push(track);
  }

  async createOffer() {
    return { type: "offer" as const, sdp: OFFER_SDP };
  }

  async setLocalDescription(description: RTCSessionDescriptionInit) {
    this.localDescription = description;
  }

  async setRemoteDescription(description: RTCSessionDescriptionInit) {
    this.remoteDescription = description;
  }

  addEventListener() {}
  removeEventListener() {}
  close() {
    this.closed = true;
  }
}

function stubMedia(track: { enabled: boolean; stop: () => void }): void {
  vi.stubGlobal("navigator", {
    mediaDevices: {
      getUserMedia: vi.fn().mockResolvedValue({
        getAudioTracks: () => [track],
        getTracks: () => [track],
      }),
    },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("session voice connection", () => {
  it("offers one audio track and a data channel, then applies the API's answer", async () => {
    const track = { enabled: true, stop: vi.fn() };
    stubMedia(track);
    const peer = new FakePeerConnection();
    vi.stubGlobal("RTCPeerConnection", function RTCPeerConnectionStub(this: unknown) {
      return peer;
    });
    const exchangeOffer = vi.fn().mockResolvedValue({
      sdp_answer: ANSWER_SDP,
      connection_id: "conn-1",
      voice_run_id: "vr-1",
    });

    const session = await connectVoice({
      exchangeOffer,
      onRemoteStream: () => undefined,
      onConnectionStateChange: () => undefined,
      onTranscriptDelta: () => undefined,
      onEventRejected: () => undefined,
      iceGatheringTimeoutMs: 100,
    });

    expect(exchangeOffer).toHaveBeenCalledWith(OFFER_SDP);
    expect(peer.channels).toEqual(["oai-events"]);
    expect(peer.added).toHaveLength(1);
    expect(peer.remoteDescription).toEqual({ type: "answer", sdp: ANSWER_SDP });
    expect(session.connectionId).toBe("conn-1");
    expect(session.voiceRunId).toBe("vr-1");

    session.setMicrophoneEnabled(false);
    expect(track.enabled).toBe(false);

    session.close();
    expect(peer.closed).toBe(true);
    expect(track.stop).toHaveBeenCalled();
  });

  it("reads transcript fragments off the data channel and reports malformed ones", async () => {
    stubMedia({ enabled: true, stop: vi.fn() });
    const peer = new FakePeerConnection();
    vi.stubGlobal("RTCPeerConnection", function RTCPeerConnectionStub(this: unknown) {
      return peer;
    });
    const onTranscriptDelta = vi.fn();
    const onEventRejected = vi.fn();

    await connectVoice({
      exchangeOffer: vi.fn().mockResolvedValue({ sdp_answer: ANSWER_SDP, connection_id: "c", voice_run_id: "v" }),
      onRemoteStream: () => undefined,
      onConnectionStateChange: () => undefined,
      onTranscriptDelta,
      onEventRejected,
      iceGatheringTimeoutMs: 100,
    });
    const deliver = (data: unknown): void => peer.dataChannel.onmessage?.({ data });

    deliver(JSON.stringify({ type: "session.output_transcript.delta", delta: "Hi Maya", start_ms: 0, end_ms: 400 }));
    deliver(JSON.stringify({ type: "session.input_transcript.delta", delta: " Hi", start_ms: 900, end_ms: 1100 }));
    deliver(JSON.stringify({ type: "session.updated" }));
    deliver(JSON.stringify({ type: "session.input_transcript.delta", delta: "no timing" }));

    expect(onTranscriptDelta.mock.calls).toEqual([
      [{ speaker: "agent", text: "Hi Maya", startMs: 0, endMs: 400 }],
      [{ speaker: "participant", text: " Hi", startMs: 900, endMs: 1100 }],
    ]);
    expect(onEventRejected).toHaveBeenCalledTimes(1);
    expect(onEventRejected.mock.calls[0]?.[0]).toContain("session.input_transcript.delta");
  });

  it("reports a refused microphone without opening a connection", async () => {
    vi.stubGlobal("navigator", {
      mediaDevices: { getUserMedia: vi.fn().mockRejectedValue(new Error("NotAllowedError")) },
    });
    vi.stubGlobal("RTCPeerConnection", FakePeerConnection);

    await expect(
      connectVoice({
        exchangeOffer: vi.fn(),
        onRemoteStream: () => undefined,
        onConnectionStateChange: () => undefined,
        onTranscriptDelta: () => undefined,
        onEventRejected: () => undefined,
        iceGatheringTimeoutMs: 100,
      }),
    ).rejects.toMatchObject({ name: "VoiceError", code: "microphone_unavailable" });
  });

  it("fails loudly when candidate gathering does not finish", async () => {
    const track = { enabled: true, stop: vi.fn() };
    stubMedia(track);
    const peer = new FakePeerConnection();
    peer.iceGatheringState = "gathering";
    vi.stubGlobal("RTCPeerConnection", function RTCPeerConnectionStub(this: unknown) {
      return peer;
    });
    const exchangeOffer = vi.fn();

    const attempt = connectVoice({
      exchangeOffer,
      onRemoteStream: () => undefined,
      onConnectionStateChange: () => undefined,
      onTranscriptDelta: () => undefined,
      onEventRejected: () => undefined,
      iceGatheringTimeoutMs: 20,
    });

    await expect(attempt).rejects.toBeInstanceOf(VoiceError);
    expect(exchangeOffer).not.toHaveBeenCalled();
    expect(peer.closed).toBe(true);
  });
});

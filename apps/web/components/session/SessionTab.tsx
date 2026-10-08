"use client";

import { ExternalLink } from "lucide-react";
import { useCallback, useEffect, useRef, useState, useTransition, type JSX } from "react";

import type { SessionControlAction, SessionProjection } from "@/lib/api/schemas";
import {
  fetchBindingOffer,
  openPreviewHandoff,
  openVoiceConnection,
  refreshSessionProjection,
  sendSessionControl,
} from "@/lib/session/actions";
import { ExtensionError, deliverBindingOffer } from "@/lib/session/extension";
import { subscribeToSession, type RealtimeStatus } from "@/lib/session/realtime";
import { appendTranscriptDelta, type TranscriptDelta, type TranscriptTurn } from "@/lib/session/transcript";
import { VoiceError, connectVoice, type VoiceSession } from "@/lib/session/voice";

import { EndedCard } from "./EndedCard";
import { PreflightPanel } from "./PreflightPanel";
import { ProgressTrail } from "./ProgressTrail";
import { SessionCharacter } from "./SessionCharacter";
import { SessionControls } from "./SessionControls";
import { TranscriptPanel } from "./TranscriptPanel";

/** How long the tab gives itself to join the voice call's room. */
const VOICE_JOIN_TIMEOUT_MS = 20_000;
/**
 * How many times in a row a lost voice connection is reopened before the tab asks
 * the participant to reconnect: at once, then after `VOICE_REOPEN_DELAY_MS`, three
 * times that, and so on. A call that stays up for `VOICE_STABLE_MS` resets the count.
 */
const VOICE_REOPEN_ATTEMPTS = 4;
const VOICE_REOPEN_DELAY_MS = 1000;
const VOICE_STABLE_MS = 30_000;

type VoiceStatus = "idle" | "connecting" | "connected" | "failed";

/**
 * How the call is carried: a Daily call with the voice agent, or the fixture's
 * replayed conversation when the control plane is the in-repository fixture.
 */
export type VoiceTransport = "daily" | "fixture";

/** The rig's canvas size: large on a roomy screen, smaller when height or width is short. */
const CHARACTER_SIZE = { roomy: 300, compact: 200 } as const;
const COMPACT_QUERY = "(max-height: 780px), (max-width: 720px)";

function useCharacterSize(): number {
  const [compact, setCompact] = useState(false);
  useEffect(() => {
    const query = window.matchMedia(COMPACT_QUERY);
    const update = (): void => setCompact(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return compact ? CHARACTER_SIZE.compact : CHARACTER_SIZE.roomy;
}

/**
 * The uTrace session tab: the live anchor for one Interview Session.
 *
 * It renders what the control plane's durable projection says and nothing it
 * inferred. Supabase Realtime only delivers newer revisions of that projection,
 * a control is applied from the revision the control plane returns, and voice
 * runs over a Daily call the uTrace API opens.
 */
/** Which runtime the session's preview serves: the base, or the candidate ready for validation. */
type PreviewRuntimeStage = "base" | "candidate";

export function SessionTab(
  props: Readonly<{
    initialProjection: SessionProjection;
    extensionId: string;
    voiceTransport: VoiceTransport;
  }>,
): JSX.Element {
  const sessionId = props.initialProjection.session_id;
  const { extensionId, voiceTransport } = props;
  const [projection, setProjection] = useState<SessionProjection>(props.initialProjection);
  const [realtimeStatus, setRealtimeStatus] = useState<RealtimeStatus>("connecting");
  const [voice, setVoice] = useState<VoiceSession | null>(null);
  const [voiceFailure, setVoiceFailure] = useState<string | null>(null);
  // The browser refused to play the agent's voice until the participant interacts.
  const [audioBlocked, setAudioBlocked] = useState(false);
  // Reopening failed too many times in a row; the participant reconnects by hand.
  const [voiceGaveUp, setVoiceGaveUp] = useState(false);
  // Bumped to replace the voice connection with a new one.
  const [voiceRun, setVoiceRun] = useState(0);
  const voiceReopens = useRef(0);
  const [notice, setNotice] = useState<string | null>(null);
  const [returned, setReturned] = useState(false);
  const [turns, setTurns] = useState<readonly TranscriptTurn[]>([]);
  const [pending, startTransition] = useTransition();
  const characterSize = useCharacterSize();

  const audio = useRef<HTMLAudioElement>(null);
  const offerDeliveredFor = useRef<PreviewRuntimeStage | null>(null);

  const ended = projection.state === "ended";
  const inSetup = projection.state === "participant_setup";
  const wantsVoice = !inSetup && !ended && projection.state !== "paused";
  const reconnecting = projection.state === "reconnecting";
  const wasReconnecting = useRef(reconnecting);
  const conversational = projection.readiness.conversational;
  const voiceStatus = describeVoice(wantsVoice, voice, voiceFailure, conversational);
  const microphoneEnabled = projection.controls.microphone === "unmuted";
  // The preview moves to a new runtime when the candidate is ready, so the extension
  // gets a fresh offer for it then and reloads the preview tab onto it.
  const previewStage: PreviewRuntimeStage =
    projection.implementation_progress === "ready_for_validation" ? "candidate" : "base";

  const acceptProjection = useCallback((next: SessionProjection): void => {
    setProjection((current) => (next.revision < current.revision ? current : next));
  }, []);

  const acceptTranscriptDelta = useCallback((delta: TranscriptDelta): void => {
    setTurns((current) => appendTranscriptDelta(current, delta));
  }, []);

  useEffect(
    () =>
      subscribeToSession({
        sessionId,
        onProjection: acceptProjection,
        onStatus: setRealtimeStatus,
      }),
    [sessionId, acceptProjection],
  );

  const playAgent = useCallback((): void => {
    const element = audio.current;
    if (element === null) return;
    element.play().then(
      () => setAudioBlocked(false),
      (error: unknown) => {
        if (error instanceof DOMException && error.name === "NotAllowedError") {
          setAudioBlocked(true);
        } else if (!(error instanceof DOMException && error.name === "AbortError")) {
          setNotice(
            `uTrace could not play the agent's voice: ${error instanceof Error ? error.message : String(error)}`,
          );
        }
      },
    );
  }, []);

  useEffect(() => {
    // Any interaction lets the browser play the agent's voice, so the first one
    // anywhere on the tab unlocks it, not only the "Turn on sound" button.
    if (!audioBlocked) return;
    window.addEventListener("pointerdown", playAgent);
    window.addEventListener("keydown", playAgent);
    return () => {
      window.removeEventListener("pointerdown", playAgent);
      window.removeEventListener("keydown", playAgent);
    };
  }, [audioBlocked, playAgent]);

  /** Reopen the voice connection after a delay, unless it has failed too many times in a row. */
  const reopenVoice = useCallback((): number | undefined => {
    const attempt = voiceReopens.current;
    if (attempt >= VOICE_REOPEN_ATTEMPTS) {
      setVoiceFailure("The voice call could not be restored.");
      setVoiceGaveUp(true);
      return undefined;
    }
    voiceReopens.current = attempt + 1;
    return window.setTimeout(
      () => setVoiceRun((run) => run + 1),
      VOICE_REOPEN_DELAY_MS * (2 ** attempt - 1),
    );
  }, []);

  useEffect(() => {
    // The voice agent ends the call once this tab has been gone from it for a
    // while, and the session then reports `reconnecting`: the session outlives
    // any one call, so a new connection replaces the ended one.
    let reopen: number | undefined;
    if (reconnecting && !wasReconnecting.current) {
      reopen = reopenVoice();
    }
    wasReconnecting.current = reconnecting;
    return () => window.clearTimeout(reopen);
  }, [reconnecting, reopenVoice]);

  useEffect(() => {
    if (!wantsVoice) {
      return;
    }
    let cancelled = false;
    let opened: VoiceSession | null = null;
    let reopen: number | undefined;
    let stable: number | undefined;

    void (async () => {
      try {
        if (voiceTransport === "fixture") {
          const { connectFixtureVoice } = await import("@/lib/session/fixture-voice");
          if (cancelled) return;
          opened = connectFixtureVoice({ onTranscriptDelta: acceptTranscriptDelta });
          setVoice(opened);
          return;
        }
        const session = await connectVoice({
          openConnection: async () => {
            const result = await openVoiceConnection(sessionId);
            if (!result.ok) {
              throw new VoiceError("join_failed", result.message, {});
            }
            return result.value;
          },
          onAgentAudio: (track) => {
            if (audio.current !== null) {
              audio.current.srcObject = new MediaStream([track]);
              playAgent();
            }
          },
          onDisconnected: () => {
            if (cancelled) return;
            setVoiceFailure("The voice connection dropped. uTrace is trying to restore it.");
            window.clearTimeout(stable);
            reopen = reopenVoice();
          },
          onTranscriptDelta: acceptTranscriptDelta,
          onNotice: setNotice,
          joinTimeoutMs: VOICE_JOIN_TIMEOUT_MS,
        });
        if (cancelled) {
          session.close();
          return;
        }
        opened = session;
        setVoice(session);
        setVoiceFailure(null);
        stable = window.setTimeout(() => {
          voiceReopens.current = 0;
        }, VOICE_STABLE_MS);
      } catch (error) {
        if (cancelled) {
          return;
        }
        reopen = reopenVoice();
        setVoiceFailure(
          error instanceof VoiceError
            ? error.message
            : "The voice connection could not be established.",
        );
      }
    })();

    return () => {
      cancelled = true;
      window.clearTimeout(reopen);
      window.clearTimeout(stable);
      opened?.close();
      setVoice(null);
    };
  }, [wantsVoice, sessionId, voiceTransport, acceptTranscriptDelta, voiceRun, playAgent, reopenVoice]);

  useEffect(() => {
    // Speech before the voice agent's pipeline attaches would have no durable
    // transcript, so the microphone opens only once the backend listens.
    voice?.setMicrophoneEnabled(microphoneEnabled && conversational);
  }, [voice, microphoneEnabled, conversational]);

  useEffect(() => {
    if (inSetup || ended || offerDeliveredFor.current === previewStage) {
      return;
    }
    offerDeliveredFor.current = previewStage;
    void (async () => {
      const result = await fetchBindingOffer(sessionId);
      if (!result.ok) {
        setNotice(result.message);
        return;
      }
      try {
        await deliverBindingOffer(extensionId, sessionId, result.value);
      } catch (error) {
        setNotice(
          error instanceof ExtensionError
            ? error.message
            : "The uTrace extension could not be given this session's binding offer.",
        );
      }
    })();
  }, [inSetup, ended, previewStage, sessionId, extensionId]);

  const refresh = useCallback((): void => {
    startTransition(async () => {
      const result = await refreshSessionProjection(sessionId);
      if (!result.ok) {
        setNotice(result.message);
        return;
      }
      acceptProjection(result.value);
    });
  }, [sessionId, acceptProjection]);

  const applyControl = (action: SessionControlAction): void => {
    startTransition(async () => {
      setNotice(null);
      const result = await sendSessionControl(sessionId, action);
      if (!result.ok) {
        setNotice(result.message);
        return;
      }
      const ack = result.value;
      setProjection((current) =>
        ack.revision < current.revision
          ? current
          : { ...current, revision: ack.revision, state: ack.state, controls: ack.controls },
      );
    });
  };

  const showPreview = (): void => {
    const previewTab = window.open("about:blank", "_blank");
    if (previewTab !== null) {
      previewTab.opener = null;
    }
    startTransition(async () => {
      setNotice(null);
      const result = await openPreviewHandoff(sessionId);
      if (!result.ok) {
        previewTab?.close();
        setNotice(result.message);
        return;
      }
      if (previewTab === null) {
        setNotice("Your browser blocked the preview tab. Allow pop-ups for uTrace and try again.");
        return;
      }
      previewTab.location.replace(result.value.front_door_url);
    });
  };

  if (ended) {
    return (
      <div className="ss-single" data-testid="session-tab" data-state="ended">
        <EndedCard
          reason={projection.end_reason}
          returned={returned}
          onReturn={() => setReturned(true)}
        />
      </div>
    );
  }

  if (inSetup) {
    return (
      <div className="ss-single" data-testid="session-tab" data-state="participant_setup">
        <PreflightPanel sessionId={sessionId} extensionId={extensionId} onPassed={refresh} />
      </div>
    );
  }

  return (
    <div
      className="ss"
      data-testid="session-tab"
      data-state={projection.state}
      data-phase={projection.phase}
      data-revision={projection.revision}
    >
      <section className="ss-stage" aria-label="Call">
        <header className="ss-head">
          <span className="ss-context">
            {projection.client_name} ·{" "}
            {projection.purpose === "validation" ? "Validation session" : "Interview session"}
          </span>
          <span className="ss-pill" data-testid="session-connection" data-tone={voiceStatus}>
            <i aria-hidden="true" />
            {CONNECTION_LABELS[voiceStatus]}
          </span>
        </header>

        <div className="ss-center">
          <div className="ss-figure" style={{ width: characterSize, height: characterSize }}>
            <SessionCharacter
              colour={projection.agent_character}
              projection={projection.character}
              size={characterSize}
              onError={setNotice}
            />
          </div>

          <p className="ss-status" data-testid="session-status">
            {projection.status_text}
          </p>
          <p className="ss-agent" data-testid="session-agent">
            {projection.agent_name}, a product agent helping the {projection.client_name} team.
          </p>

          {audioBlocked ? (
            <button
              type="button"
              className="pr-btn ss-preview"
              onClick={playAgent}
              data-testid="session-audio-unlock"
            >
              Turn on sound
            </button>
          ) : null}

          {projection.implementation_progress === null ? null : (
            <ProgressTrail progress={projection.implementation_progress} />
          )}

          {projection.preview_available ? (
            <button
              type="button"
              className="pr-btn ss-preview"
              onClick={showPreview}
              disabled={pending}
              data-testid="session-open-preview"
            >
              Open preview
              <ExternalLink size={16} aria-hidden="true" />
            </button>
          ) : null}
        </div>

        <div className="ss-notices">
          {projection.readiness.observation ? null : (
            <p className="pr-message pr-message--quiet" data-testid="session-readiness">
              {projection.readiness.unavailable_detail ??
                "Waiting on your preview before asking about what you are doing on screen."}
            </p>
          )}
          {voiceFailure === null ? null : (
            <p className="pr-message" role="status" data-testid="session-voice-failure">
              {voiceFailure}{" "}
              {voiceGaveUp ? (
                <button
                  type="button"
                  className="pr-link"
                  onClick={() => {
                    voiceReopens.current = 0;
                    setVoiceGaveUp(false);
                    setVoiceRun((run) => run + 1);
                  }}
                  data-testid="session-voice-retry"
                >
                  Reconnect
                </button>
              ) : null}
            </p>
          )}
          {notice === null ? null : (
            <p className="pr-message" role="alert" data-testid="session-notice">
              {notice}
            </p>
          )}
          {realtimeStatus === "live" ? null : (
            <p className="pr-note" data-testid="session-realtime-status">
              {realtimeStatus === "connecting"
                ? "Connecting to live updates."
                : "Live updates are unavailable, so this shows the last state uTrace recorded."}
            </p>
          )}
        </div>

        <SessionControls controls={projection.controls} pending={pending} onAction={applyControl} />
      </section>

      <TranscriptPanel
        turns={turns}
        agentName={projection.agent_name}
        agentColour={projection.agent_character}
        live={voiceStatus === "connected"}
      />
      <audio ref={audio} data-testid="session-audio" />
    </div>
  );
}

const CONNECTION_LABELS: Readonly<Record<VoiceStatus, string>> = {
  idle: "Not connected",
  connecting: "Connecting",
  connected: "Connected",
  failed: "Reconnecting",
};

function describeVoice(
  wantsVoice: boolean,
  voice: VoiceSession | null,
  failure: string | null,
  conversational: boolean,
): VoiceStatus {
  if (!wantsVoice) return "idle";
  if (failure !== null) return "failed";
  return voice === null || !conversational ? "connecting" : "connected";
}

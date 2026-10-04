"use client";

import { Clock, Mic, MicOff, Pause, PhoneOff, Play } from "lucide-react";
import { useState, type JSX } from "react";

import type { ControlsState, SessionControlAction } from "@/lib/api/schemas";

const ICON = 17;

/**
 * Mute, Pause, Finish later and End interview.
 *
 * Only the actions the control plane currently offers are enabled, and nothing
 * here changes the session's own state: the caller applies whatever revision
 * the control plane returns. Ending is the cancellation action, so it needs a
 * second, explicit confirmation.
 */
export function SessionControls(
  props: Readonly<{
    controls: ControlsState;
    pending: boolean;
    onAction: (action: SessionControlAction) => void;
  }>,
): JSX.Element {
  const [confirmingEnd, setConfirmingEnd] = useState(false);
  const offers = (action: SessionControlAction): boolean => props.controls.available.includes(action);
  const muted = props.controls.microphone === "muted";
  const resumable = offers("resume");

  if (confirmingEnd) {
    return (
      <div className="ss-controls" data-testid="session-controls" data-microphone={props.controls.microphone}>
        <p className="ss-end-warning" data-testid="session-end-warning">
          Ending stops this case for good. To step away and come back instead, choose Finish later.
        </p>
        <div className="ss-control-row">
          <button
            type="button"
            className="ss-control ss-control--end"
            disabled={props.pending}
            onClick={() => props.onAction("end_confirm")}
            data-testid="session-control-end-confirm"
          >
            <PhoneOff size={ICON} aria-hidden="true" />
            End it, I&rsquo;m done
          </button>
          <button
            type="button"
            className="ss-control"
            disabled={props.pending}
            onClick={() => setConfirmingEnd(false)}
            data-testid="session-control-end-cancel"
          >
            Keep going
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="ss-controls" data-testid="session-controls" data-microphone={props.controls.microphone}>
      <div className="ss-control-row">
        <button
          type="button"
          className="ss-control"
          aria-pressed={muted}
          disabled={props.pending || !offers(muted ? "unmute" : "mute")}
          onClick={() => props.onAction(muted ? "unmute" : "mute")}
          data-testid="session-control-mute"
        >
          {muted ? <MicOff size={ICON} aria-hidden="true" /> : <Mic size={ICON} aria-hidden="true" />}
          {muted ? "Unmute" : "Mute"}
        </button>
        <button
          type="button"
          className="ss-control"
          aria-pressed={resumable}
          disabled={props.pending || !(offers("pause") || resumable)}
          onClick={() => props.onAction(resumable ? "resume" : "pause")}
          data-testid="session-control-pause"
        >
          {resumable ? <Play size={ICON} aria-hidden="true" /> : <Pause size={ICON} aria-hidden="true" />}
          {resumable ? "Resume" : "Pause"}
        </button>
        <button
          type="button"
          className="ss-control"
          disabled={props.pending || !offers("finish_later")}
          onClick={() => props.onAction("finish_later")}
          data-testid="session-control-finish-later"
        >
          <Clock size={ICON} aria-hidden="true" />
          Finish later
        </button>
        <button
          type="button"
          className="ss-control ss-control--end"
          disabled={props.pending || !offers("end_confirm")}
          onClick={() => setConfirmingEnd(true)}
          data-testid="session-control-end"
        >
          <PhoneOff size={ICON} aria-hidden="true" />
          End interview
        </button>
      </div>
    </div>
  );
}

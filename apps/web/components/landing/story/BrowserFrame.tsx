"use client";

import { ChevronLeft, ChevronRight, Lock, Minus, MoreVertical, Plus, RotateCw, Square, X } from "lucide-react";
import Image from "next/image";
import type { JSX, ReactNode } from "react";

import { ShopalyticsMark } from "../strip/surfaces/discord";
import type { Surface } from "./beats";

const URL: Readonly<Record<"session" | "preview", string>> = { session: "utrace.dev/session/shopalytics", preview: "shopalytics-9f1c2e.preview.utrace.dev/conversion" };
const WORKSPACE_TITLE = "uTrace · Implementation workspace";

/**
 * One browser window for the whole story. The session and preview tabs share its tab strip,
 * so "the preview tab" is literal; the workspace and the Case File open as uTrace windows
 * in the same frame with a plain title bar. The Case File's bar carries nothing but the window
 * controls, because the document names itself in its own heading.
 */
export function BrowserFrame(props: Readonly<{ surface: Surface; previewVersion: string; children: ReactNode }>): JSX.Element {
  const tabbed = props.surface === "session" || props.surface === "preview";
  return (
    <div className="st-frame" data-testid="story-frame" data-surface={props.surface}>
      {tabbed ? (
        <>
          <div className="st-tabs">
            <span className={"st-tab" + (props.surface === "session" ? " is-active" : "")}>
              <Image src="/brand/utrace-logo.svg" alt="" width={16} height={16} unoptimized />
              uTrace · Session
              <X size={13} />
            </span>
            <span className={"st-tab" + (props.surface === "preview" ? " is-active" : "")}>
              <i className="st-tab-mark">
                <ShopalyticsMark size={10} />
              </i>
              Shopalytics · Preview
              <X size={13} />
            </span>
            <span className="st-tab-add">
              <Plus size={15} />
            </span>
          </div>
          <div className="st-address">
            <ChevronLeft size={18} />
            <ChevronRight size={18} />
            <RotateCw size={15} />
            <span className="st-url">
              <Lock size={12} />
              {props.surface === "preview" ? URL.preview.replace("9f1c2e", props.previewVersion) : URL.session}
            </span>
            <MoreVertical size={16} />
          </div>
        </>
      ) : (
        <div className="st-title">
          {props.surface === "workspace" ? WORKSPACE_TITLE : null}
          <span className="st-window-controls">
            <Minus size={15} />
            <Square size={12} />
            <X size={15} />
          </span>
        </div>
      )}
      <div className="st-body">{props.children}</div>
    </div>
  );
}

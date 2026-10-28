"use client";

import { ChevronLeft, ChevronRight, Lock, Minus, MoreVertical, Plus, RotateCw, Square, X } from "lucide-react";
import Image from "next/image";
import type { JSX, ReactNode } from "react";

import { DISCORD_MARK_IMAGE, ShopalyticsLogo } from "../surfaces/discord";
import type { Surface } from "./beats";

const WORKSPACE_TITLE = "uTrace · Implementation workspace";

function address(surface: "discord" | "session" | "preview", discordView: "channel" | "dm", previewVersion: string): string {
  switch (surface) {
    case "discord":
      return discordView === "channel" ? "discord.com/channels/shopalytics/feedback" : "discord.com/channels/@me/utrace";
    case "session":
      return "utrace.dev/session/shopalytics";
    case "preview":
      return `shopalytics-${previewVersion}.preview.utrace.dev/conversion`;
  }
}

/**
 * One browser window for the whole demo. Maya starts in Discord; joining the interview opens
 * the session and preview tabs beside it, so "the preview tab" is literal. The workspace and the
 * Case File open as uTrace windows in the same frame with a plain title bar; the Case File's bar
 * carries nothing but the window controls, because the document names itself in its own heading.
 */
export function BrowserFrame(props: Readonly<{ surface: Surface; joined: boolean; discordView: "channel" | "dm"; previewVersion: string; children: ReactNode }>): JSX.Element {
  const { surface } = props;
  return (
    <div className="st-frame" data-testid="story-frame" data-surface={surface}>
      {surface === "discord" || surface === "session" || surface === "preview" ? (
        <>
          <div className="st-tabs">
            <span className={"st-tab" + (surface === "discord" ? " is-active" : "")} data-testid="story-tab-discord">
              <Image {...DISCORD_MARK_IMAGE} alt="" width={16} height={16} />
              Discord
              <X size={13} />
            </span>
            {props.joined ? (
              <>
                <span className={"st-tab" + (surface === "session" ? " is-active" : "")} data-testid="story-tab-session">
                  <Image src="/brand/utrace-logo.svg" alt="" width={16} height={16} unoptimized />
                  uTrace · Session
                  <X size={13} />
                </span>
                <span className={"st-tab" + (surface === "preview" ? " is-active" : "")} data-testid="story-tab-preview">
                  <ShopalyticsLogo size={16} className="st-tab-mark" />
                  Shopalytics · Preview
                  <X size={13} />
                </span>
              </>
            ) : null}
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
              {address(surface, props.discordView, props.previewVersion)}
            </span>
            <MoreVertical size={16} />
          </div>
        </>
      ) : (
        <div className="st-title">
          {surface === "workspace" ? WORKSPACE_TITLE : null}
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

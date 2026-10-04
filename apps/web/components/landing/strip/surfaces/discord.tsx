"use client";

import "@fontsource-variable/noto-sans";
import { Bell, Check, ChevronDown, Gift, Hash, Headphones, HelpCircle, Inbox, Mic, Phone, Pin, Plus, Search, Settings, Smile, Sticker, UserPlus, Users, Video } from "lucide-react";
import Image, { type ImageProps } from "next/image";
import type { JSX, ReactNode } from "react";

import "./discord.css";

const MAYA_AVATAR = "/landing/strip/maya-avatar.webp";
const UTRACE_AVATAR = "/brand/utrace-logo.svg";

/** An avatar's image props: served as it is, so every size shares one download (Maya's is a 12 KB, 256px WebP). */
export function avatarImage(app: boolean, size: number): ImageProps {
  return { src: app ? UTRACE_AVATAR : MAYA_AVATAR, alt: "", width: size, height: size, unoptimized: true };
}

export const DISCORD_MARK_IMAGE: ImageProps = { src: "/source-logos/discord.svg", alt: "", width: 24, height: 24 };

/**
 * Discord, reproduced rather than sketched: the server rail, channel list, chat column,
 * message groups, app tag, embed, component buttons and composer at Discord's own
 * proportions and palette. The strip crops these; nothing here is comic-treated.
 */
export function DiscordFrame(props: Readonly<{ view: "channel" | "dm"; sidebar: "none" | "rail" | "full"; children: ReactNode; testId: string }>): JSX.Element {
  return (
    <div className="dc" data-testid={props.testId} data-view={props.view} data-sidebar={props.sidebar}>
      {props.sidebar !== "none" ? <ServerRail /> : <span />}
      {props.sidebar === "full" ? props.view === "channel" ? <ChannelList /> : <DmList /> : <span />}
      <section className="dc-chat">{props.children}</section>
    </div>
  );
}

function ServerRail(): JSX.Element {
  return (
    <aside className="dc-rail" aria-hidden="true">
      <span className="dc-rail-home">
        <Image {...DISCORD_MARK_IMAGE} alt="" style={{ filter: "brightness(0) invert(1)" }} />
      </span>
      <hr />
      <span className="dc-rail-server">
        <ShopalyticsMark size={20} />
      </span>
      <span className="dc-rail-add">
        <Plus size={20} />
      </span>
    </aside>
  );
}

/** Shopalytics' own mark: three rising bars, white on the black server tile. */
export function ShopalyticsMark(props: Readonly<{ size: number; colour?: string }>): JSX.Element {
  return (
    <svg width={props.size} height={props.size} viewBox="0 0 20 20" aria-hidden="true">
      <rect x="2" y="11" width="4" height="7" rx="1" fill={props.colour ?? "currentColor"} />
      <rect x="8" y="6" width="4" height="12" rx="1" fill={props.colour ?? "currentColor"} />
      <rect x="14" y="2" width="4" height="16" rx="1" fill={props.colour ?? "currentColor"} />
    </svg>
  );
}

function ChannelList(): JSX.Element {
  return (
    <aside className="dc-side" aria-hidden="true">
      <div className="dc-side-header">
        Shopalytics
        <ChevronDown size={16} />
      </div>
      <div className="dc-side-body">
        <div className="dc-category">
          <ChevronDown size={10} />
          Community
        </div>
        {["welcome", "general", "feedback", "product-updates"].map((channel) => (
          <div key={channel} className={"dc-channel" + (channel === "feedback" ? " is-selected" : "")}>
            <Hash size={18} />
            {channel}
          </div>
        ))}
        <div className="dc-category">
          <ChevronDown size={10} />
          Voice
        </div>
        <div className="dc-channel">
          <Headphones size={16} />
          Lounge
        </div>
      </div>
      <AccountPanel />
    </aside>
  );
}

function DmList(): JSX.Element {
  return (
    <aside className="dc-side" aria-hidden="true">
      <div className="dc-find">Find or start a conversation</div>
      <div className="dc-side-body">
        <div className="dc-dm">
          <Users size={18} />
          Friends
        </div>
        <div className="dc-category">Direct messages</div>
        <div className="dc-dm is-selected">
          <Avatar app size={30} />
          <span>
            uTrace
            <small>Online</small>
          </span>
        </div>
      </div>
      <AccountPanel />
    </aside>
  );
}

function AccountPanel(): JSX.Element {
  return (
    <div className="dc-account">
      <Avatar size={30} online />
      <div>
        Maya
        <small>Online</small>
      </div>
      <Mic size={16} />
      <Headphones size={16} />
      <Settings size={16} />
    </div>
  );
}

export function Avatar(props: Readonly<{ size: number; app?: boolean; online?: boolean }>): JSX.Element {
  return (
    <span className={"dc-avatar" + (props.app ? " is-app" : "")} style={{ width: props.size, height: props.size }}>
      <Image {...avatarImage(props.app === true, props.size)} alt="" />
      {props.online ? <i className="dc-status" /> : null}
    </span>
  );
}

export function ChannelHeader(props: Readonly<{ channel: string; topic: string }>): JSX.Element {
  return (
    <header className="dc-chat-header">
      <Hash size={22} />
      {props.channel}
      <span className="dc-topic">{props.topic}</span>
      <span className="dc-tools">
        <Bell size={18} />
        <Pin size={18} />
        <Users size={18} />
        <Search size={18} />
        <Inbox size={18} />
        <HelpCircle size={18} />
      </span>
    </header>
  );
}

export function DmHeader(): JSX.Element {
  return (
    <header className="dc-chat-header">
      <Avatar app size={24} />
      uTrace
      <AppTag />
      <span className="dc-tools">
        <Phone size={18} />
        <Video size={18} />
        <UserPlus size={18} />
        <Search size={18} />
        <HelpCircle size={18} />
      </span>
    </header>
  );
}

export function AppTag(): JSX.Element {
  return (
    <span className="dc-tag">
      <Check size={10} strokeWidth={3} />
      APP
    </span>
  );
}

export function DayDivider(props: Readonly<{ label: string }>): JSX.Element {
  return <div className="dc-day">{props.label}</div>;
}

export function NewDivider(): JSX.Element {
  return (
    <div className="dc-new" aria-hidden="true">
      <span>NEW</span>
    </div>
  );
}

export function Message(props: Readonly<{ author: "Maya" | "uTrace"; time: string; children: ReactNode; faded?: boolean; testId?: string }>): JSX.Element {
  const app = props.author === "uTrace";
  return (
    <div className={"dc-msg" + (props.faded ? " is-faded" : "")} data-testid={props.testId}>
      <Avatar app={app} size={36} />
      <div className="dc-msg-body">
        <div className="dc-msg-head">
          <span className="dc-msg-name">{props.author}</span>
          {app ? <AppTag /> : null}
          <span className="dc-msg-time">{props.time}</span>
        </div>
        <div className="dc-msg-text">{props.children}</div>
      </div>
    </div>
  );
}

export function Typing(props: Readonly<{ who: string }>): JSX.Element {
  return (
    <div className="dc-typing" data-testid="strip-discord-typing">
      <i />
      <i />
      <i />
      <strong>{props.who}</strong> is typing…
    </div>
  );
}

export function DiscordButton(props: Readonly<{ secondary?: boolean; pressed?: boolean; children: ReactNode; testId?: string }>): JSX.Element {
  return (
    <span className={"dc-btn" + (props.secondary ? " is-secondary" : "") + (props.pressed ? " is-pressed" : "")} data-testid={props.testId}>
      {props.children}
    </span>
  );
}

export function Embed(props: Readonly<{ title: string; text: string; meta?: string; signal?: boolean; testId?: string; children?: ReactNode }>): JSX.Element {
  return (
    <div className={"dc-embed" + (props.signal ? " is-signal" : "")} data-testid={props.testId}>
      <div className="dc-embed-title">{props.title}</div>
      <p className="dc-embed-text">{props.text}</p>
      {props.meta !== undefined ? <div className="dc-embed-meta">{props.meta}</div> : null}
      {props.children}
    </div>
  );
}

export function Composer(props: Readonly<{ placeholder: string }>): JSX.Element {
  return (
    <div className="dc-composer" aria-hidden="true">
      <i className="dc-plus">
        <Plus size={14} strokeWidth={3} />
      </i>
      <span>{props.placeholder}</span>
      <Gift size={20} />
      <Sticker size={20} />
      <Smile size={20} />
    </div>
  );
}

export function DmIntro(): JSX.Element {
  return (
    <div className="dc-dm-intro">
      <Avatar app size={56} />
      <h3>
        uTrace <AppTag />
      </h3>
      <p>This is the beginning of your direct message history with uTrace.</p>
    </div>
  );
}

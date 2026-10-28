"use client";

import "@fontsource-variable/noto-sans";
import { Bell, CalendarDays, Check, ChevronDown, Compass, Gift, Hash, Headphones, HelpCircle, Inbox, ListTree, Mic, Phone, Pin, Plus, Search, Settings, Smile, Sticker, UserPlus, Users, Video } from "lucide-react";
import Image, { type ImageProps } from "next/image";
import type { JSX, ReactNode } from "react";

import { SHOPALYTICS } from "../story/shopalyticsFixture";
import "./discord.css";

const UTRACE_AVATAR = "/brand/utrace-logo.svg";

export const DISCORD_MARK_IMAGE: ImageProps = { src: "/source-logos/discord.svg", alt: "", width: 24, height: 24 };

/**
 * Who posts: Maya, whose avatar is Dudulemon's mark; the uTrace app; or another member on one of
 * Discord's default avatars (its mark, white, on a colour).
 */
export type Author = Readonly<{ name: string; avatar: "maya" | "app" | Readonly<{ colour: string }> }>;
export const MAYA: Author = { name: "Maya", avatar: "maya" };
export const UTRACE: Author = { name: "uTrace", avatar: "app" };

/** An avatar's image props: served as it is, so every size shares one download. */
function avatarImage(avatar: "maya" | "app", size: number): ImageProps {
  return { src: avatar === "app" ? UTRACE_AVATAR : SHOPALYTICS.accountLogoSrc, alt: "", width: size, height: size, unoptimized: true };
}

/**
 * Discord's desktop client, reproduced rather than sketched, at its own proportions and
 * palette: the server rail, the channel or DM list with the account panel, and the chat column
 * with its header, message groups, app tag, embeds, component buttons and composer.
 */
export function DiscordFrame(props: Readonly<{ view: "channel" | "dm"; children: ReactNode; testId: string }>): JSX.Element {
  return (
    <div className="dc" data-testid={props.testId} data-view={props.view}>
      <ServerRail view={props.view} />
      {props.view === "channel" ? <ChannelList /> : <DmList />}
      <section className="dc-chat">{props.children}</section>
    </div>
  );
}

function ServerRail(props: Readonly<{ view: "channel" | "dm" }>): JSX.Element {
  return (
    <aside className="dc-rail" aria-hidden="true">
      <span className={"dc-rail-home" + (props.view === "dm" ? " is-selected" : "")}>
        <Image {...DISCORD_MARK_IMAGE} alt="" style={{ filter: "brightness(0) invert(1)" }} />
      </span>
      <hr />
      <span className={"dc-rail-server" + (props.view === "channel" ? " is-selected" : "")}>
        <ShopalyticsLogo size={48} />
      </span>
      <span className="dc-rail-add">
        <Plus size={22} />
      </span>
      <span className="dc-rail-add">
        <Compass size={22} />
      </span>
    </aside>
  );
}

/** Shopalytics' logo, the shopping bag with a rising line; its container rounds the corners. */
export function ShopalyticsLogo(props: Readonly<{ size: number; className?: string }>): JSX.Element {
  return <Image src={SHOPALYTICS.clientLogoSrc} alt="" width={props.size} height={props.size} unoptimized className={props.className} />;
}

const CHANNELS: readonly (readonly [string, readonly string[]])[] = [
  ["Information", ["welcome", "announcements", "product-updates"]],
  ["Community", ["general", "feedback", "show-and-tell", "help"]],
];

function ChannelList(): JSX.Element {
  return (
    <aside className="dc-side" aria-hidden="true">
      <div className="dc-side-header">
        Shopalytics
        <ChevronDown size={18} />
      </div>
      <div className="dc-side-body">
        <div className="dc-side-link">
          <CalendarDays size={18} />
          Events
        </div>
        <div className="dc-side-link">
          <ListTree size={18} />
          Browse Channels
        </div>
        <hr className="dc-side-rule" />
        {CHANNELS.map(([category, channels]) => (
          <div key={category}>
            <div className="dc-category">
              <ChevronDown size={11} />
              {category}
            </div>
            {channels.map((channel) => (
              <div key={channel} className={"dc-channel" + (channel === "feedback" ? " is-selected" : "")}>
                <Hash size={20} />
                {channel}
              </div>
            ))}
          </div>
        ))}
        <div className="dc-category">
          <ChevronDown size={11} />
          Voice
        </div>
        <div className="dc-channel">
          <Headphones size={18} />
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
      <div className="dc-side-header dc-side-header--find">
        <span className="dc-find">Find or start a conversation</span>
      </div>
      <div className="dc-side-body">
        <div className="dc-side-link">
          <Users size={20} />
          Friends
        </div>
        <div className="dc-category dc-category--dm">
          Direct Messages
          <Plus size={15} />
        </div>
        <div className="dc-dm is-selected">
          <Avatar author={UTRACE} size={32} />
          <span>
            uTrace
            <small>Shopalytics&rsquo; product agent</small>
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
      <Avatar author={MAYA} size={32} online />
      <div>
        Maya
        <small>Online</small>
      </div>
      <Mic size={18} />
      <Headphones size={18} />
      <Settings size={18} />
    </div>
  );
}

export function Avatar(props: Readonly<{ author: Author; size: number; online?: boolean }>): JSX.Element {
  const { avatar } = props.author;
  return (
    <span className={"dc-avatar" + (avatar === "app" ? " is-app" : "")} style={{ width: props.size, height: props.size, background: typeof avatar === "object" ? avatar.colour : undefined }}>
      {typeof avatar === "object" ? <Image {...DISCORD_MARK_IMAGE} alt="" className="dc-avatar-default" /> : <Image {...avatarImage(avatar, props.size)} alt="" />}
      {props.online ? <i className="dc-status" /> : null}
    </span>
  );
}

export function ChannelHeader(props: Readonly<{ channel: string; topic: string }>): JSX.Element {
  return (
    <header className="dc-chat-header">
      <Hash size={24} />
      {props.channel}
      <span className="dc-topic">{props.topic}</span>
      <span className="dc-tools">
        <Bell size={20} />
        <Pin size={20} />
        <Users size={20} />
        <span className="dc-search">
          Search
          <Search size={14} />
        </span>
        <Inbox size={20} />
        <HelpCircle size={20} />
      </span>
    </header>
  );
}

export function DmHeader(): JSX.Element {
  return (
    <header className="dc-chat-header">
      <Avatar author={UTRACE} size={24} />
      uTrace
      <AppTag />
      <span className="dc-tools">
        <Phone size={20} />
        <Video size={20} />
        <Pin size={20} />
        <UserPlus size={20} />
        <span className="dc-search">
          Search
          <Search size={14} />
        </span>
        <Inbox size={20} />
        <HelpCircle size={20} />
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

/** The top of a direct message: the other side's avatar and name over the start of the history. */
export function DmIntro(): JSX.Element {
  return (
    <div className="dc-dm-intro" aria-hidden="true">
      <Avatar author={UTRACE} size={80} />
      <strong>uTrace</strong>
      <span>uTrace</span>
      <p>
        This is the beginning of your direct message history with <b>uTrace</b>.
      </p>
    </div>
  );
}

/** The rule Discord draws between days; red with a NEW tag when the unread messages start there. */
export function DateDivider(props: Readonly<{ date: string; unread: boolean }>): JSX.Element {
  return (
    <div className={"dc-date" + (props.unread ? " is-new" : "")} aria-hidden="true">
      <span>{props.date}</span>
      {props.unread ? <b>NEW</b> : null}
    </div>
  );
}

export function Message(props: Readonly<{ author: Author; time: string; children: ReactNode; testId?: string }>): JSX.Element {
  return (
    <div className="dc-msg" data-testid={props.testId}>
      <Avatar author={props.author} size={40} />
      <div className="dc-msg-body">
        <div className="dc-msg-head">
          <span className="dc-msg-name">{props.author.name}</span>
          {props.author.avatar === "app" ? <AppTag /> : null}
          <span className="dc-msg-time">{props.time}</span>
        </div>
        <div className="dc-msg-text">{props.children}</div>
      </div>
    </div>
  );
}

export function DiscordButton(props: Readonly<{ secondary?: boolean; children: ReactNode; testId?: string }>): JSX.Element {
  return (
    <span className={"dc-btn" + (props.secondary ? " is-secondary" : "")} data-testid={props.testId}>
      {props.children}
    </span>
  );
}

/** A rich embed; `signal` takes the Product Signal's coral bar, `credit` Discord's green. */
export function Embed(props: Readonly<{ title: string; text: string; meta?: string; signal?: boolean; credit?: boolean; testId?: string; children?: ReactNode }>): JSX.Element {
  return (
    <div className={"dc-embed" + (props.signal ? " is-signal" : "") + (props.credit ? " is-credit" : "")} data-testid={props.testId}>
      <div className="dc-embed-title">{props.title}</div>
      <p className="dc-embed-text">{props.text}</p>
      {props.meta !== undefined ? <div className="dc-embed-meta">{props.meta}</div> : null}
      {props.children}
    </div>
  );
}

/** The message box, with who is typing just under it, where Discord shows it. */
export function Composer(props: Readonly<{ placeholder: string; typing: string | null }>): JSX.Element {
  return (
    <div className="dc-composer-wrap">
      <div className="dc-composer" aria-hidden="true">
        <i className="dc-plus">
          <Plus size={16} strokeWidth={3} />
        </i>
        <span>{props.placeholder}</span>
        <Gift size={22} />
        <Sticker size={22} />
        <Smile size={22} />
      </div>
      {props.typing !== null ? (
        <div className="dc-typing" data-testid="discord-typing">
          <i />
          <i />
          <i />
          <strong>{props.typing}</strong> is typing…
        </div>
      ) : null}
    </div>
  );
}

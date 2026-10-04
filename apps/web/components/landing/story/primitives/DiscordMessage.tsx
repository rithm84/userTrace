import type { CSSProperties, JSX, ReactNode } from "react";

const FONT = '"gg sans", "Noto Sans", "Helvetica Neue", Helvetica, Arial, sans-serif';

/**
 * One Discord message as it renders in the dark theme: 40px avatar, the
 * author in 16px/500 #F2F3F5, the timestamp in 12px #949BA4, the body in
 * 16px/1.375 #DBDEE1 on the #313338 chat surface.
 */
export function DiscordMessage(props: Readonly<{
  author: string;
  avatarSrc: string;
  time: string;
  text: ReactNode;
  app?: boolean;
  style?: CSSProperties;
  className?: string;
}>): JSX.Element {
  return (
    <div
      className={props.className}
      data-testid="story-discord-message"
      style={{
        display: "flex",
        gap: 16,
        alignItems: "flex-start",
        width: 400,
        padding: "12px 16px 14px",
        background: "#313338",
        borderRadius: 8,
        fontFamily: FONT,
        color: "#DBDEE1",
        boxSizing: "border-box",
        ...props.style,
      }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element -- static 128px asset */}
      <img
        alt=""
        src={props.avatarSrc}
        width={40}
        height={40}
        style={{ width: 40, height: 40, borderRadius: "50%", flex: "none", marginTop: 2 }}
      />
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 6, lineHeight: "1.375rem" }}>
          <span style={{ fontSize: 16, fontWeight: 500, color: "#F2F3F5" }}>{props.author}</span>
          {props.app ? <span className="discord-app-tag">APP</span> : null}
          <span style={{ fontSize: 12, fontWeight: 500, color: "#949BA4" }}>{props.time}</span>
        </div>
        <p style={{ margin: 0, fontSize: 16, lineHeight: 1.375, color: "#DBDEE1", whiteSpace: "pre-wrap" }}>
          {props.text}
        </p>
      </div>
    </div>
  );
}

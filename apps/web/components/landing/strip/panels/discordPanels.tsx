"use client";

import { Check, Eye } from "lucide-react";
import type { JSX } from "react";

import { SHOPALYTICS } from "../../story/shopalyticsFixture";
import { CursorArrow } from "../art";
import { CUE, passed, stepsSince } from "../cues";
import { Caption, MayaArt } from "../pieces";
import { ChannelHeader, Composer, DiscordButton, DiscordFrame, DmHeader, Embed, Message, NewDivider, Typing } from "../surfaces/discord";

export const REPLY_TEXT = "Thanks, Maya. I'm uTrace, Shopalytics' product agent. I'll DM you so we can look at that workflow together.";
export const DM_TEXT = "Hi Maya, could you show me the view you rebuild each Monday? We'll talk inside a prepared Shopalytics preview. Your live store stays untouched.";
// the close-up states the Outreach Offer, the moment a reader wonders why Maya would say yes, on its own line so Maya's hand never covers it; the DM above already says where the interview happens
export const INVITE = Object.freeze({ title: "Let's see your Monday workflow", text: `A short voice interview.\nDudulemon gets $${SHOPALYTICS.offer.credit} off its next bill.`, short: "A short voice interview.", meta: "Private invitation · only for Maya" });

/** Panel 4: the request lands in #feedback. The coral agent's burst is drawn by the strip, over the frame. */
export function RequestPanel(props: Readonly<{ elapsed: number }>): JSX.Element {
  const posted = passed(props.elapsed, CUE.requestPost);
  return (
    <>
      <DiscordFrame view="channel" sidebar="rail" testId="strip-discord-request">
        <ChannelHeader channel="feedback" topic="Ideas, requests, and the little things" />
        <div className="dc-scroll">
          {posted ? (
            <>
              <NewDivider />
              <Message author="Maya" time="9:02 AM" testId="strip-request-message">
                {SHOPALYTICS.request.text}
              </Message>
            </>
          ) : (
            <Typing who="Maya" />
          )}
        </div>
        <Composer placeholder="Message #feedback" />
      </DiscordFrame>
      <Caption on={passed(props.elapsed, CUE.request + 250)} style={{ right: 58, top: 8, width: 172 }} testId="strip-caption-request">
        9:02. She says it out loud.
      </Caption>
    </>
  );
}

/** Panel 6: the public reply, the corroboration control, and (drawn by the strip) the Product Signal card. */
export function ReplyPanel(props: Readonly<{ elapsed: number }>): JSX.Element {
  const typing = passed(props.elapsed, CUE.replyTyping) && !passed(props.elapsed, CUE.replyPost);
  const posted = passed(props.elapsed, CUE.replyPost);
  const count = stepsSince(props.elapsed, CUE.replyCorroborate, CUE.replyCorroborateStep, 3);
  return (
    <>
      <DiscordFrame view="channel" sidebar="none" testId="strip-discord-reply">
        <div className="dc-scroll">
          {posted ? (
            <Message author="uTrace" time="9:03 AM" testId="strip-reply-message">
              {REPLY_TEXT}
              <div className="dc-actions">
                <DiscordButton secondary testId="strip-corroborate">
                  <Eye size={16} />
                  I&rsquo;m seeing this too
                  {count > 0 ? <b data-testid="strip-corroborate-count">· {count}</b> : null}
                </DiscordButton>
              </div>
              {passed(props.elapsed, CUE.replySignal) ? <Embed signal testId="strip-signal-embed" title="Product Signal" text="Saved conversion views · Feature request" meta="A real gap in the product, worth a conversation." /> : null}
            </Message>
          ) : null}
          {typing ? <Typing who="uTrace" /> : null}
        </div>
      </DiscordFrame>
      <Caption on={passed(props.elapsed, CUE.reply + 250)} style={{ right: 14, top: 98, width: 150 }} testId="strip-caption-reply">
        One Product Signal.
      </Caption>
    </>
  );
}

/** Panel 7: the private invitation, only to Maya. */
export function InvitePanel(props: Readonly<{ elapsed: number }>): JSX.Element {
  const messaged = passed(props.elapsed, CUE.inviteMessage);
  const card = passed(props.elapsed, CUE.inviteCard);
  const button = passed(props.elapsed, CUE.inviteButton);
  return (
    <>
      <DiscordFrame view="dm" sidebar="rail" testId="strip-discord-invite">
        <DmHeader />
        <div className="dc-scroll">
          {messaged ? (
            <Message author="uTrace" time="9:03 AM" testId="strip-invite-message">
              {DM_TEXT}
              {card ? (
                <Embed title={INVITE.title} text={INVITE.short}>
                  {button ? (
                    <div className="dc-actions">
                      <DiscordButton testId="strip-invite-button">Join interview</DiscordButton>
                    </div>
                  ) : null}
                </Embed>
              ) : null}
            </Message>
          ) : (
            <Typing who="uTrace" />
          )}
        </div>
      </DiscordFrame>
      <Caption on={passed(props.elapsed, CUE.invite + 250)} style={{ right: 16, top: 8, width: 160 }} testId="strip-caption-invite">
        Only Maya gets the invitation.
      </Caption>
    </>
  );
}

/** Ten held positions along a curve from the frame's right to the button, spaced ease-out so the hand slows into the click. */
const CURSOR_PATH: readonly (readonly [number, number])[] = Array.from({ length: 10 }, (_, index) => {
  const t = 1 - Math.pow(1 - (index + 1) / 10, 2);
  // the tip lands on the button's centre; the arrow body stays inside the frame
  const [x0, y0] = [610, 150];
  const [cx, cy] = [430, 96];
  const [x1, y1] = [202, 176];
  return [Math.round((1 - t) * (1 - t) * x0 + 2 * (1 - t) * t * cx + t * t * x1), Math.round((1 - t) * (1 - t) * y0 + 2 * (1 - t) * t * cy + t * t * y1)] as const;
});

/**
 * Panel 8: the invitation up close, Maya's hand on the mouse, and a real button. The
 * reader's click is the transition into the story; the drawn cursor only hovers over it.
 */
export function ClickPanel(props: Readonly<{ elapsed: number; joined: boolean; onJoin: () => void }>): JSX.Element {
  const step = stepsSince(props.elapsed, CUE.clickCursor, CUE.clickCursorStep, CURSOR_PATH.length);
  const cursor = CURSOR_PATH[Math.max(0, step - 1)];
  // the moment the cursor lands, the button takes its hover colour until the scroll presses it
  const arrived = step === CURSOR_PATH.length;
  const hovering = arrived && !props.joined;
  return (
    <>
      <div className="strip-click-ground" />
      <div className="strip-click-embed" style={{ transform: "scale(1.34)" }}>
        <Embed title={INVITE.title} text={INVITE.text} meta={INVITE.meta}>
          <div className="dc-actions">
            {/* once pressed, the button becomes the receipt Discord shows for a used interaction: grey, "Joined", no longer a control */}
            <button type="button" className={"dc-btn strip-join" + (props.joined ? " is-joined" : "") + (hovering ? " is-hover" : "")} data-testid="strip-join" aria-label={props.joined ? "Interview joined" : "Join the interview and follow the story"} disabled={props.joined} onClick={props.onJoin}>
              {props.joined ? <><Check size={15} strokeWidth={2.6} aria-hidden="true" /> Joined</> : "Join interview"}
            </button>
          </div>
        </Embed>
      </div>
      <MayaArt piece="hand-mouse" style={{ right: -44, bottom: -26 }} />
      {step > 0 ? <CursorArrow size={24} style={{ position: "absolute", left: cursor[0], top: cursor[1], transition: "none" }} /> : null}
      <Caption on={passed(props.elapsed, CUE.clickCaption)} style={{ right: 14, top: 22, width: 150 }} testId="strip-caption-click">
        Let&rsquo;s follow the case.
      </Caption>
    </>
  );
}

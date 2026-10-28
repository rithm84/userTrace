"use client";

import { Check, Eye } from "lucide-react";
import { useRef, type JSX } from "react";

import { CursorArrow } from "../art";
import { ChannelHeader, Composer, DateDivider, DiscordButton, DiscordFrame, DmHeader, DmIntro, Embed, MAYA, Message, UTRACE, type Author } from "../surfaces/discord";
import { pointerAt } from "./agentScene";
import { PRODUCT_AGENT, type Beat } from "./beats";
import { SHOPALYTICS } from "./shopalyticsFixture";
import { useTargets } from "./useTargets";

/** The uTrace app posts; the agent introduces itself by name, as the product's agent does. */
export const REPLY_TEXT = `Thanks, Maya. I'm ${PRODUCT_AGENT.name}, a product agent helping the Shopalytics team. I'll DM you so we can look at that workflow together.`;
export const DM_TEXT = `Hi Maya, it's ${PRODUCT_AGENT.name}. Could you show me the view you rebuild each Monday? We'll talk inside a prepared Shopalytics preview. Your live store stays untouched.`;
/** The invitation states the Outreach Offer, the moment a reader wonders why Maya would say yes. */
export const INVITE = Object.freeze({ title: "Let's see your Monday workflow", text: `A short voice interview.\n${SHOPALYTICS.account} gets $${SHOPALYTICS.offer.credit} off its next bill.`, meta: "Private invitation · only for you" });
export const THANKS_TEXT = "Thanks for walking me through your Monday, Maya. Your saved views are with the Shopalytics team now.";
/** The reward, hinted here; For your users tells the rest. */
export const CREDIT = Object.freeze({ title: "Interview complete", text: `${SHOPALYTICS.account} gets $${SHOPALYTICS.offer.credit} off its next Shopalytics bill.`, meta: "Applied to the next invoice automatically" });

const JONAS: Author = { name: "jonas", avatar: { colour: "#5865f2" } };
/** When things happen in each Discord beat, in ms into the beat. */
const AT = Object.freeze({ requestPost: 1100, replyPost: 700, corroborate: [1500, 2200, 2900], invitePost: 600, pointer: 2000, press: 3800, thanksPost: 600 });

/**
 * The Discord tab: Maya's request in the Shopalytics server's #feedback, uTrace's public reply
 * that others can second, the private invitation she joins from, and, once the work is
 * validated, uTrace's thanks with the credit her company earned. Join interview is a real
 * button: it takes the reader on into the interview.
 */
export function DiscordTab(props: Readonly<{ beat: Beat; progress: number; width: number; onJoin: () => void }>): JSX.Element {
  const { beat } = props;
  const t = props.progress * beat.duration;
  const root = useRef<HTMLDivElement>(null);
  const channel = beat.id === "request" || beat.id === "signal";
  const targets = useTargets(root, props.width, `${beat.id}|${t >= AT.invitePost}`);
  const pointer = beat.id === "invite" ? pointerAt([{ at: AT.pointer, target: "join" }], targets, { x: props.width * 0.78, y: 470 }, t) : null;
  const joined = beat.id === "thanks" || (beat.id === "invite" && t >= AT.press);
  const hovering = pointer !== null && !joined && targets.join !== undefined && Math.hypot(pointer.x - (targets.join.x + targets.join.width / 2), pointer.y - (targets.join.y + targets.join.height / 2)) < 2;
  return (
    <div ref={root} className="st-discord" data-testid="story-discord" data-beat={beat.id}>
      {channel ? (
        <DiscordFrame view="channel" testId="story-discord-channel">
          <ChannelHeader channel="feedback" topic="Ideas, requests, and the little things" />
          <div className="dc-scroll">
            <DateDivider date="October 2, 2026" unread={false} />
            <Message author={JONAS} time="Yesterday at 4:18 PM">
              The new cohort chart is great. Any chance it gets a weekly view too?
            </Message>
            {beat.id === "signal" || t >= AT.requestPost ? (
              <>
                <DateDivider date="October 3, 2026" unread />
                <Message author={MAYA} time="Today at 9:02 AM" testId="story-request-message">
                  {SHOPALYTICS.request.text}
                </Message>
              </>
            ) : null}
            {beat.id === "signal" && t >= AT.replyPost ? <Reply t={t} /> : null}
          </div>
          <Composer placeholder="Message #feedback" typing={beat.id === "request" && t < AT.requestPost ? "Maya" : beat.id === "signal" && t < AT.replyPost ? "uTrace" : null} />
        </DiscordFrame>
      ) : (
        <DiscordFrame view="dm" testId="story-discord-dm">
          <DmHeader />
          <div className="dc-scroll">
            <DmIntro />
            {beat.id === "thanks" || t >= AT.invitePost ? <DateDivider date="October 3, 2026" unread={false} /> : null}
            {beat.id === "thanks" || t >= AT.invitePost ? (
              <Message author={UTRACE} time="Today at 9:03 AM" testId="story-invite-message">
                {DM_TEXT}
                <Embed title={INVITE.title} text={INVITE.text} meta={INVITE.meta}>
                  <div className="dc-actions">
                    {/* once pressed, the button becomes the receipt Discord shows for a used interaction: grey, "Joined", no longer a control */}
                    <button type="button" className={"dc-btn st-join" + (joined ? " is-joined" : "") + (hovering ? " is-hover" : "")} data-target="join" data-testid="story-join" aria-label={joined ? "Interview joined" : "Join the interview and skip to it"} disabled={joined} onClick={props.onJoin}>
                      {joined ? (
                        <>
                          <Check size={15} strokeWidth={2.6} aria-hidden="true" /> Joined
                        </>
                      ) : (
                        "Join interview"
                      )}
                    </button>
                  </div>
                </Embed>
              </Message>
            ) : null}
            {beat.id === "thanks" && t >= AT.thanksPost ? (
              <Message author={UTRACE} time="Today at 9:41 AM" testId="story-thanks-message">
                {THANKS_TEXT}
                <Embed title={CREDIT.title} text={CREDIT.text} meta={CREDIT.meta} credit testId="story-credit-embed" />
              </Message>
            ) : null}
          </div>
          <Composer placeholder="Message @uTrace" typing={(beat.id === "invite" && t < AT.invitePost) || (beat.id === "thanks" && t < AT.thanksPost) ? "uTrace" : null} />
        </DiscordFrame>
      )}
      {pointer !== null ? <CursorArrow size={20} style={{ position: "absolute", left: pointer.x - 2, top: pointer.y - 1, zIndex: 5 }} /> : null}
    </div>
  );
}

/** uTrace's public reply, with the corroboration control counting the people seeing it too; the Product Signal stays private. */
function Reply(props: Readonly<{ t: number }>): JSX.Element {
  const count = AT.corroborate.filter((at) => props.t >= at).length;
  return (
    <Message author={UTRACE} time="Today at 9:03 AM" testId="story-reply-message">
      {REPLY_TEXT}
      <div className="dc-actions">
        <DiscordButton secondary testId="story-corroborate">
          <Eye size={16} />
          I&rsquo;m seeing this too
          {count > 0 ? <b data-testid="story-corroborate-count">· {count}</b> : null}
        </DiscordButton>
      </div>
    </Message>
  );
}

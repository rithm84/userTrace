"use client";

import type { JSX } from "react";

import { CUE, passed } from "../cues";
import { Caption } from "../pieces";
import { AppStoreReview, RedditPost, XPost } from "../surfaces/clippings";

/**
 * Panel 5: the same job, said three more ways on three other Customer Surfaces. The cards
 * cascade from the top-left: each is fully inside the frame when it lands, and its key line
 * (a title, a post) stays clear of the card that lands on top of it. DOM order is arrival
 * order, so a later card is never underneath.
 */
export function EchoPanel(props: Readonly<{ elapsed: number }>): JSX.Element {
  return (
    <>
      <div className="strip-echo-ground" />
      <div className={"strip-clip" + (passed(props.elapsed, CUE.echoReddit) ? " is-on" : "")} style={{ left: 10, top: 14, rotate: "-1.5deg" }}>
        <RedditPost community="r/shopalytics" age="2d" title="Anyone else redoing the same conversion filters every week?" votes={57} comments={24} />
      </div>
      <div className={"strip-clip" + (passed(props.elapsed, CUE.echoAppStore) ? " is-on" : "")} style={{ left: 96, top: 100, rotate: "1.2deg" }}>
        <AppStoreReview app="Shopalytics" title="Great data, no saved views." stars={2} date="Oct 3" reviewer="lena.k" body="Love the insights. Wish I could pick up where I left off instead of setting every filter again." />
      </div>
      <div className={"strip-clip" + (passed(props.elapsed, CUE.echoX) ? " is-on" : "")} style={{ left: 26, top: 158, rotate: "-0.8deg" }}>
        <XPost name="Sam Okafor" handle="samops" initial="S" body="rebuilding it weekly is my whole monday" />
      </div>
      <Caption on={passed(props.elapsed, CUE.echo + 250)} style={{ right: 70, top: 8, width: 104 }} testId="strip-caption-echo">
        Not just her.
      </Caption>
    </>
  );
}

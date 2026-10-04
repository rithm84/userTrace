"use client";

import "@fontsource-variable/reddit-sans";
import { ArrowBigDown, ArrowBigUp, MessageCircle, MoreHorizontal, Share2, Star } from "lucide-react";
import Image, { type ImageProps } from "next/image";
import type { JSX } from "react";

import "./clippings.css";

export const REDDIT_MARK_IMAGE: ImageProps = { src: "/source-logos/reddit.svg", alt: "", width: 24, height: 24 };
export const APP_STORE_MARK_IMAGE: ImageProps = { src: "/source-logos/app-store.svg", alt: "", width: 16, height: 16, unoptimized: true };
export const X_MARK_IMAGE: ImageProps = { src: "/source-logos/x.svg", alt: "X", width: 22, height: 22 };

/** A Reddit post as the 2024 feed renders it: community row, join pill, title, vote and comment pills. */
export function RedditPost(props: Readonly<{ community: string; age: string; title: string; votes: number; comments: number }>): JSX.Element {
  return (
    <article className="rd" data-testid="strip-reddit">
      <div className="rd-head">
        <Image {...REDDIT_MARK_IMAGE} alt="" />
        {props.community}
        <span>· {props.age}</span>
        <MoreHorizontal className="rd-more" size={18} />
      </div>
      <h4>{props.title}</h4>
      <div className="rd-foot">
        <span className="rd-pill is-up">
          <ArrowBigUp size={18} strokeWidth={1.75} />
          {props.votes}
          <ArrowBigDown size={18} strokeWidth={1.75} />
        </span>
        <span className="rd-pill">
          <MessageCircle size={16} strokeWidth={1.75} />
          {props.comments}
        </span>
        <span className="rd-pill">
          <Share2 size={16} strokeWidth={1.75} />
          Share
        </span>
      </div>
    </article>
  );
}

/** An App Store review card: title, orange stars, date and reviewer, body. */
export function AppStoreReview(props: Readonly<{ app: string; title: string; stars: 1 | 2 | 3 | 4 | 5; date: string; reviewer: string; body: string }>): JSX.Element {
  return (
    <article className="as" data-testid="strip-appstore">
      <div className="as-head">
        <Image {...APP_STORE_MARK_IMAGE} alt="" />
        App Store
        <span>{props.app}</span>
      </div>
      <h4>{props.title}</h4>
      <div className="as-meta">
        <span className="as-stars" aria-label={`${props.stars} of 5 stars`}>
          {[1, 2, 3, 4, 5].map((n) => (
            <Star key={n} size={13} fill={n <= props.stars ? "currentColor" : "none"} strokeWidth={n <= props.stars ? 0 : 1.5} />
          ))}
        </span>
        <span>
          {props.date} · {props.reviewer}
        </span>
      </div>
      <p>{props.body}</p>
    </article>
  );
}

/** An X post in the embedded-post format: avatar, name and handle, the X mark, the post. */
export function XPost(props: Readonly<{ name: string; handle: string; initial: string; body: string }>): JSX.Element {
  return (
    <article className="xp" data-testid="strip-x">
      <div className="xp-head">
        <span className="xp-avatar">{props.initial}</span>
        <div className="xp-who">
          <strong>{props.name}</strong>
          <span>@{props.handle}</span>
        </div>
        <Image {...X_MARK_IMAGE} className="xp-logo" alt="X" />
      </div>
      <p>{props.body}</p>
    </article>
  );
}

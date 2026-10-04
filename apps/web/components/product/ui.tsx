import type { JSX, ReactNode } from "react";

/** Shared product primitives. One panel, one chip, one tile; no comic devices. */

export type ChipTone = "neutral" | "active" | "waiting" | "closed";

export function Chip(props: Readonly<{ tone: ChipTone; children: ReactNode; testId: string }>): JSX.Element {
  const suffix = props.tone === "neutral" ? "" : ` pr-chip--${props.tone}`;
  return (
    <span className={`pr-chip${suffix}`} data-testid={props.testId} data-tone={props.tone}>
      {props.children}
    </span>
  );
}

export function Panel(
  props: Readonly<{
    title: string;
    aside: ReactNode;
    variant: "plain" | "raised" | "core" | "mutable";
    testId: string;
    children: ReactNode;
  }>,
): JSX.Element {
  const suffix = props.variant === "plain" ? "" : ` pr-panel--${props.variant}`;
  return (
    <section className={`pr-panel${suffix}`} data-testid={props.testId} data-variant={props.variant}>
      <div className="pr-panel-head">
        <h2 className="pr-h2">{props.title}</h2>
        {props.aside}
      </div>
      {props.children}
    </section>
  );
}

export function StatTile(
  props: Readonly<{ term: string; value: string; detail: string | null; testId: string }>,
): JSX.Element {
  return (
    <div className="pr-tile" data-testid={props.testId}>
      <dl style={{ margin: 0 }}>
        <dt>{props.term}</dt>
        <dd>
          {props.value}
          {props.detail === null ? null : <small>{props.detail}</small>}
        </dd>
      </dl>
    </div>
  );
}

export function Rows(props: Readonly<{ children: ReactNode; testId: string }>): JSX.Element {
  return (
    <ul className="pr-rows" data-testid={props.testId}>
      {props.children}
    </ul>
  );
}

export function Row(
  props: Readonly<{ term: string; children: ReactNode; testId: string }>,
): JSX.Element {
  return (
    <li data-testid={props.testId}>
      <span className="pr-row-term">{props.term}</span>
      <span className="pr-row-detail">{props.children}</span>
    </li>
  );
}

export function EmptyState(props: Readonly<{ children: ReactNode; testId: string }>): JSX.Element {
  return (
    <p className="pr-empty" data-testid={props.testId}>
      {props.children}
    </p>
  );
}

/** Integer minor units rendered in the offer's own currency. */
export function formatMinorUnits(minorUnits: number, currency: string): string {
  return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(minorUnits / 100);
}

/** A recorded timestamp, rendered without pretending to a time zone we do not know. */
export function formatTimestamp(value: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
  }).format(new Date(value));
}

/** `feature_request_flow` reads as "Feature Request Flow"; states stay snake_case in the DOM. */
export function humanize(value: string): string {
  return value.replaceAll("_", " ").replace(/^./u, (first) => first.toUpperCase());
}

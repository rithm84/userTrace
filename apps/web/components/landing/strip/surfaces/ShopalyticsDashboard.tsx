"use client";

import { ArrowDownRight, ArrowUpRight, Bookmark, Calendar, Check, ChevronDown, Download, FileText, LayoutDashboard, MessageSquare, Plus, Settings, ShoppingBag, TrendingUp, Users, X } from "lucide-react";
import Image from "next/image";
import { Fragment, memo, useMemo, type JSX } from "react";

import type { FilterChip } from "../../story/shopalyticsFixture";
import { ShopalyticsMark, avatarImage } from "./discord";
import "./dashboard.css";

const NAV = [
  ["Overview", LayoutDashboard],
  ["Conversion", TrendingUp],
  ["Customers", Users],
  ["Products", ShoppingBag],
  ["Reviews", MessageSquare],
  ["Reports", FileText],
  ["Settings", Settings],
] as const;

const SERIES = [2.02, 2.11, 2.08, 2.19, 2.14, 2.27, 2.21, 2.3, 2.24, 2.36, 2.41, 2.33, 2.29, 2.38, 2.46, 2.4, 2.35, 2.44, 2.52, 2.47, 2.39, 2.5, 2.58, 2.51, 2.43, 2.55, 2.61, 2.54, 2.49, 2.31];
const PRODUCTS: readonly (readonly [string, number])[] = [
  ["Align leggings", 3.1],
  ["Ribbed tank", 2.6],
  ["Crop hoodie", 2.2],
  ["Flow shorts", 1.9],
  ["Everyday dress", 1.4],
];

export type DashboardProps = Readonly<{
  /** Applied filters, in the order they were added. */
  filters: readonly FilterChip[];
  /** 0..1: how much of the daily line has been drawn. */
  chart: number;
  /** A route change: the page paints white for a frame. */
  flash: boolean;
  /** The chip that just landed pops on two frames. */
  popLast: boolean;
  /** A criterion the restore failed to bring back: its slot is drawn empty and dashed where it stood (`at`, its place among the chips). */
  missing?: Readonly<{ criterion: FilterChip["criterion"]; at: number }> | null;
  /** The candidate's Save view control exists. */
  saveView?: boolean;
  /** The candidate's Saved views list; undefined hides the section. */
  savedViews?: readonly string[];
  activeView?: string | null;
  /** A transient confirmation at the bottom centre. */
  toast?: string | null;
}>;

function chartPoint(index: number): readonly [number, number] {
  return [(index / (SERIES.length - 1)) * 300, 90 - ((SERIES[index] - 1.9) / 0.8) * 84];
}

/** The daily line as a Catmull-Rom spline, so a crop never shows a jagged polyline. */
function linePath(progress: number): string {
  const count = Math.max(2, Math.round(SERIES.length * progress));
  const points = Array.from({ length: count }, (_, index) => chartPoint(index));
  let d = `M${points[0][0].toFixed(1)},${points[0][1].toFixed(1)}`;
  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[Math.max(0, i - 1)];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[Math.min(points.length - 1, i + 2)];
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += ` C${c1[0].toFixed(1)},${c1[1].toFixed(1)} ${c2[0].toFixed(1)},${c2[1].toFixed(1)} ${p2[0].toFixed(1)},${p2[1].toFixed(1)}`;
  }
  return d;
}

/** The empty, dashed place of a filter the restore did not bring back. */
function MissingSlot(props: Readonly<{ criterion: FilterChip["criterion"] }>): JSX.Element {
  return <span className="sh-missing" data-testid="strip-dashboard-missing" data-criterion={props.criterion} />;
}

/**
 * The Shopalytics conversion dashboard, 640 x 420 at its natural size: sidebar with the
 * Dudulemon workspace, the Conversion report, applied filters, three KPIs, the daily line
 * and the top products. Rendered straight-on, or mapped onto the drawn laptop by the desk panel.
 */
export const ShopalyticsDashboard = memo(function ShopalyticsDashboard(props: DashboardProps): JSX.Element {
  const filtered = props.filters.length >= 4;
  const missing = props.missing ?? null;
  const path = useMemo(() => linePath(props.chart), [props.chart]);
  const lastIndex = Math.max(1, Math.round(SERIES.length * props.chart)) - 1;
  const [lastX, lastY] = chartPoint(lastIndex);
  return (
    <div className="sh" data-testid="strip-dashboard" data-filters={props.filters.length}>
      <aside className="sh-side" aria-hidden="true">
        <div className="sh-brand">
          <i>
            <ShopalyticsMark size={14} />
          </i>
          Shopalytics
        </div>
        <div className="sh-workspace">
          <i />
          Dudulemon
          <ChevronDown size={14} />
        </div>
        {NAV.map(([label, Icon]) => (
          <div key={label} className={"sh-nav" + (label === "Conversion" ? " is-active" : "")}>
            <Icon size={14} strokeWidth={1.9} />
            {label}
          </div>
        ))}
        {props.savedViews !== undefined ? (
          <div className="sh-views" data-testid="strip-dashboard-views">
            <small>Saved views</small>
            {props.savedViews.length === 0 ? <span className="sh-views-empty">None yet</span> : null}
            {props.savedViews.map((view) => (
              <span key={view} className={"sh-view" + (view === props.activeView ? " is-active" : "")}>
                <Bookmark size={12} />
                {view}
              </span>
            ))}
          </div>
        ) : null}
        <div className="sh-user">
          <Image {...avatarImage(false, 22)} alt="" />
          <span>
            Maya
            <small>Growth lead</small>
          </span>
        </div>
      </aside>
      <div className="sh-main">
        <div className="sh-head">
          <div>
            <h3>Conversion</h3>
            <small>dudulemon.com · {filtered ? `${props.filters.length} filters applied` : "All sessions"}</small>
          </div>
          <div className="sh-head-tools">
            <span className="sh-ghost">
              <Calendar size={13} />
              Last 30 days
              <ChevronDown size={12} />
            </span>
            <span className="sh-ghost">
              <Download size={13} />
              Export
            </span>
            {props.saveView ? (
              <span className="sh-ghost is-primary sh-save" data-testid="strip-dashboard-save">
                <Bookmark size={13} />
                Save view
              </span>
            ) : null}
          </div>
        </div>
        <div className="sh-filters" data-testid="strip-dashboard-filters">
          {props.filters.map((chip, index) => (
            <Fragment key={chip.criterion}>
              {missing !== null && missing.at === index ? <MissingSlot criterion={missing.criterion} /> : null}
              <span className={"sh-chip" + (props.popLast && index === props.filters.length - 1 ? " is-in" : "")} data-criterion={chip.criterion}>
                {chip.label}
                <X size={12} />
              </span>
            </Fragment>
          ))}
          {missing !== null && missing.at >= props.filters.length ? <MissingSlot criterion={missing.criterion} /> : null}
          <span className="sh-add">
            <Plus size={12} />
            Add filter
          </span>
          {props.filters.length === 0 ? <span className="sh-hint">No filters applied</span> : null}
        </div>
        <div className="sh-kpis">
          <div className="sh-kpi">
            <small>Conversion rate</small>
            <strong>{filtered ? "1.86%" : "2.31%"}</strong>
            <span className={filtered ? "is-down" : "is-up"}>
              {filtered ? <ArrowDownRight size={12} /> : <ArrowUpRight size={12} />}
              {filtered ? "0.45 pp below store" : "0.27 pp vs last period"}
            </span>
          </div>
          <div className="sh-kpi">
            <small>Sessions</small>
            <strong>{filtered ? "3,284" : "48,210"}</strong>
            <span>{filtered ? "TikTok · women 25–34" : "All channels"}</span>
          </div>
          <div className="sh-kpi">
            <small>Orders</small>
            <strong>{filtered ? "61" : "1,114"}</strong>
            <span>{filtered ? "Negative sizing reviews" : "Last 30 days"}</span>
          </div>
        </div>
        <div className="sh-grid">
          <div className="sh-card">
            <div className="sh-card-title">
              <span>Conversion rate, daily</span>
              <span>{filtered ? "Filtered segment" : "Store"}</span>
            </div>
            <svg className="sh-chart" viewBox="0 0 300 96" preserveAspectRatio="none" aria-hidden="true">
              {[6, 48, 90].map((y) => (
                <line key={y} x1="0" x2="300" y1={y} y2={y} stroke="#ececec" strokeWidth="1" />
              ))}
              <path d={`${path} L${lastX.toFixed(1)},96 L0,96 Z`} fill="#f1f1f1" />
              <path d={path} fill="none" stroke="#111" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
              {props.chart > 0 ? <circle cx={lastX} cy={lastY} r="4" fill="var(--coral-strong)" stroke="#111" strokeWidth="1.5" vectorEffect="non-scaling-stroke" /> : null}
            </svg>
            <div className="sh-axis">
              {["Sep 5", "Sep 12", "Sep 19", "Sep 26", "Oct 3"].map((label) => (
                <span key={label}>{label}</span>
              ))}
            </div>
          </div>
          <div className="sh-card">
            <div className="sh-card-title">
              <span>Top products</span>
            </div>
            {PRODUCTS.map(([name, rate]) => (
              <div key={name} className="sh-row">
                <span>{name}</span>
                <i style={{ ["--w" as string]: `${(rate / 3.4) * 100}%` }} />
                <b>{rate.toFixed(1)}%</b>
              </div>
            ))}
          </div>
        </div>
        {props.flash ? <div className="sh-flash" data-testid="strip-dashboard-flash" /> : null}
        {props.toast !== undefined && props.toast !== null ? (
          <div className="sh-toast" data-testid="strip-dashboard-toast">
            <Check size={13} strokeWidth={3} />
            {props.toast}
          </div>
        ) : null}
      </div>
    </div>
  );
});

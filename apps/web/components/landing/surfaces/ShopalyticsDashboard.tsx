"use client";

import { Bell, Bookmark, ChevronDown, Download, House, MessageSquare, Search, Settings, ShoppingBag, TrendingUp, Users, X } from "lucide-react";
import Image from "next/image";
import { memo, useMemo, type JSX } from "react";

import { SHOPALYTICS, type FilterChip } from "../story/shopalyticsFixture";
import { ShopalyticsLogo } from "./discord";
import "./dashboard.css";

type Criterion = FilterChip["criterion"];

const NAV = [
  ["Home", House],
  ["Conversion", TrendingUp],
  ["Products", ShoppingBag],
  ["Segments", Users],
  ["Reviews", MessageSquare],
] as const;

const DATE_PRESETS = ["Last 7 days", "Last 30 days", "Last 90 days", "Custom range"] as const;
/** The fixture's date filter, which must be one of the presets. */
function dateFilter(): FilterChip {
  const filter = SHOPALYTICS.filters.find((candidate) => candidate.criterion === "date");
  if (filter === undefined || !(DATE_PRESETS as readonly string[]).includes(filter.label)) throw new Error("The fixture's date filter is not one of Shopalytics' date presets.");
  return filter;
}
const DATE_FILTER = dateFilter();
/** Every control but the date presets, in the order the bar lays them out. */
const CONTROLS = SHOPALYTICS.filters.filter((filter) => filter.criterion !== "date");

/** What the dashboard shows: the whole store, the agreed view, or the view without its review topic. */
type Reading = "store" | "filtered" | "partial";

const KPIS: Readonly<Record<Reading, readonly (readonly [string, string, string])[]>> = {
  store: [["Sessions", "48,210", "+4.2% vs last period"], ["Orders", "1,114", "+3.1% vs last period"], ["Conversion rate", "2.31%", "+0.27 pp vs last period"], ["Revenue", "$96,880", "+5.4% vs last period"]],
  filtered: [["Sessions", "11,190", "TikTok · women 25–34"], ["Orders", "157", "Negative sizing reviews"], ["Conversion rate", "1.40%", "0.91 pp below store"], ["Revenue", "$15,420", "Dresses"]],
  partial: [["Sessions", "17,460", "TikTok · women 25–34"], ["Orders", "302", "Any review topic"], ["Conversion rate", "1.73%", "0.58 pp below store"], ["Revenue", "$29,310", "Dresses"]],
};

/** Daily conversion rate, in percent, over the last 30 days: the agreed view shows the drop-off after negative sizing reviews landed. */
const SERIES: Readonly<Record<Reading, readonly number[]>> = {
  store: [2.02, 2.11, 2.08, 2.19, 2.14, 2.27, 2.21, 2.3, 2.24, 2.36, 2.41, 2.33, 2.29, 2.38, 2.46, 2.4, 2.35, 2.44, 2.52, 2.47, 2.39, 2.5, 2.58, 2.51, 2.43, 2.55, 2.61, 2.54, 2.49, 2.31],
  filtered: [3.22, 2.76, 3.29, 1.31, 1.41, 1.07, 1.13, 1.15, 1.12, 1.38, 1.2, 1.32, 1.32, 1.07, 1.18, 1.24, 1.09, 1.31, 1.16, 1.22, 1.05, 1.27, 1.19, 1.11, 1.34, 1.08, 1.21, 1.16, 1.29, 1.4],
  partial: [2.71, 2.52, 2.83, 1.92, 1.98, 1.71, 1.76, 1.8, 1.69, 1.88, 1.74, 1.83, 1.79, 1.62, 1.71, 1.77, 1.64, 1.82, 1.7, 1.75, 1.6, 1.79, 1.72, 1.66, 1.84, 1.63, 1.74, 1.69, 1.8, 1.73],
};
const AXIS = ["Sep 4", "Sep 11", "Sep 18", "Sep 25", "Oct 2"];
const CHART = Object.freeze({ width: 300, height: 110, max: 3.5 });

const PRODUCTS: Readonly<Record<Reading, readonly (readonly [string, number])[]>> = {
  store: [["Aero High-Rise Legging", 3.1], ["Featherlight Sports Bra", 2.6], ["Flowstate Midi Dress", 2.2]],
  filtered: [["Flowstate Midi Dress", 1.1], ["Aero High-Rise Legging", 1.6], ["Featherlight Sports Bra", 1.8]],
  partial: [["Flowstate Midi Dress", 1.5], ["Aero High-Rise Legging", 1.9], ["Featherlight Sports Bra", 2.0]],
};

function reading(applied: readonly Criterion[]): Reading {
  if (applied.length === SHOPALYTICS.filters.length) return "filtered";
  if (applied.length === SHOPALYTICS.filters.length - 1 && !applied.includes("topic")) return "partial";
  return "store";
}

/** The daily line as a Catmull-Rom spline, so the chart never shows a jagged polyline. */
function linePath(series: readonly number[]): string {
  const points = series.map((value, index) => [(index / (series.length - 1)) * CHART.width, CHART.height - (value / CHART.max) * CHART.height] as const);
  let d = `M${points[0][0].toFixed(1)},${points[0][1].toFixed(1)}`;
  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[Math.max(0, i - 1)];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[Math.min(points.length - 1, i + 2)];
    d += ` C${(p1[0] + (p2[0] - p0[0]) / 6).toFixed(1)},${(p1[1] + (p2[1] - p0[1]) / 6).toFixed(1)} ${(p2[0] - (p3[0] - p1[0]) / 6).toFixed(1)},${(p2[1] - (p3[1] - p1[1]) / 6).toFixed(1)} ${p2[0].toFixed(1)},${p2[1].toFixed(1)}`;
  }
  return d;
}

export type DashboardProps = Readonly<{
  /** The filters holding Maya's values; every other control shows its default. */
  applied: readonly Criterion[];
  /** The control that just took its value pops on two frames. */
  changed: Criterion | null;
  /** A reload: the page paints white for a moment. */
  flash: boolean;
  /** The candidate's saved views row exists. */
  candidate: boolean;
  /** What is typed into the view name field. */
  viewName: string;
  savedViews: readonly string[];
  activeView: string | null;
  /** A transient confirmation at the bottom centre. */
  toast: string | null;
}>;

/**
 * Shopalytics' conversion dashboard as the product draws it, in the Dudulemon workspace: the
 * black top bar with search and the workspace, the grey sidebar, the date presets and filter
 * controls, four KPIs, the daily conversion line and the products beside it. Controls the agent
 * may point at are registered as `data-target`, as the browser SDK registers them.
 */
export const ShopalyticsDashboard = memo(function ShopalyticsDashboard(props: DashboardProps): JSX.Element {
  const read = reading(props.applied);
  const path = useMemo(() => linePath(SERIES[read]), [read]);
  const dateSet = props.applied.includes("date");
  return (
    <div className="sh" data-testid="story-dashboard" data-filters={props.applied.length} data-reading={read}>
      <header className="sh-top" aria-hidden="true">
        <span className="sh-brand">
          <ShopalyticsLogo size={24} />
          Shopalytics
        </span>
        <span className="sh-search">
          <Search size={13} />
          Search
          <kbd>⌘K</kbd>
        </span>
        <span className="sh-top-end">
          <Bell size={15} />
          <span className="sh-workspace" data-testid="story-dashboard-workspace">
            <Image src={SHOPALYTICS.accountLogoSrc} alt="" width={22} height={22} unoptimized />
            {SHOPALYTICS.account}
          </span>
        </span>
      </header>
      <aside className="sh-side" aria-hidden="true">
        {NAV.map(([label, Icon]) => (
          <span key={label} className={"sh-nav" + (label === "Conversion" ? " is-active" : "")}>
            <Icon size={14} strokeWidth={2} />
            {label}
          </span>
        ))}
        <span className="sh-nav sh-nav-end">
          <Settings size={14} strokeWidth={2} />
          Settings
        </span>
      </aside>
      <main className="sh-main">
        <div className="sh-head">
          <h3>Conversion</h3>
          {props.candidate ? (
            <div className="sh-views" data-testid="story-dashboard-views">
              <span className="sh-input" data-target="view-name">
                {props.viewName.length === 0 ? <span className="sh-placeholder">View name</span> : props.viewName}
                {props.viewName.length > 0 && props.viewName.length < SHOPALYTICS.savedViewName.length ? <i className="sh-caret" /> : null}
              </span>
              <span className={"sh-button is-primary" + (props.viewName.length === 0 ? " is-disabled" : "")} data-target="save-view">
                Save view
              </span>
              {props.savedViews.map((view) => (
                <span key={view} className={"sh-button sh-view" + (view === props.activeView ? " is-active" : "")} data-target="saved-view">
                  <Bookmark size={12} />
                  {view}
                </span>
              ))}
            </div>
          ) : (
            <span className="sh-button">
              <Download size={13} />
              Export
            </span>
          )}
        </div>
        <section className="sh-card sh-filters" data-target="filter-bar">
          <div className="sh-filter-top">
            <span className="sh-segmented">
              {DATE_PRESETS.map((preset) => (
                <span key={preset} className={(dateSet ? preset === DATE_FILTER.label : preset === DATE_PRESETS[0]) ? "is-on" : undefined} data-target={preset === DATE_FILTER.label ? "filter-date" : undefined}>
                  {preset}
                </span>
              ))}
            </span>
            <span className="sh-filter-count">
              {props.applied.length === 0 ? "No filters" : `${props.applied.length} ${props.applied.length === 1 ? "filter" : "filters"}`}
              <span className="sh-reset">
                <X size={12} />
                Reset
              </span>
            </span>
          </div>
          <div className="sh-controls">
            {CONTROLS.map((control) => {
              const on = props.applied.includes(control.criterion);
              return (
                <span key={control.criterion} className={"sh-control" + (on ? " is-set" : "") + (props.changed === control.criterion ? " is-in" : "")} data-target={`filter-${control.criterion}`} data-criterion={control.criterion}>
                  <small>{control.dimension}</small>
                  {on ? control.label : "All"}
                  <ChevronDown size={12} />
                </span>
              );
            })}
          </div>
        </section>
        <div className="sh-kpis">
          {KPIS[read].map(([label, value, note]) => (
            <div key={label} className="sh-card sh-kpi">
              <small>{label}</small>
              <strong>{value}</strong>
              <span className={read === "store" ? "is-up" : "is-down"}>{note}</span>
            </div>
          ))}
        </div>
        <div className="sh-grid">
          <div className="sh-card sh-chart-card" data-target="chart">
            <div className="sh-card-title">
              <span>Conversion rate, daily</span>
              <span>{read === "store" ? "All sessions" : "Filtered"}</span>
            </div>
            <svg className="sh-chart" viewBox={`0 0 ${CHART.width} ${CHART.height}`} preserveAspectRatio="none" aria-hidden="true">
              {[0.25, 0.5, 0.75].map((k) => (
                <line key={k} x1="0" x2={CHART.width} y1={CHART.height * k} y2={CHART.height * k} stroke="#ebebeb" strokeWidth="1" vectorEffect="non-scaling-stroke" />
              ))}
              <path d={`${path} L${CHART.width},${CHART.height} L0,${CHART.height} Z`} fill="#12acf01f" />
              <path d={path} fill="none" stroke="#12acf0" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
            </svg>
            <div className="sh-axis">
              {AXIS.map((label) => (
                <span key={label}>{label}</span>
              ))}
            </div>
          </div>
          <div className="sh-card">
            <div className="sh-card-title">
              <span>Products</span>
              <span>Conv.</span>
            </div>
            {PRODUCTS[read].map(([name, rate]) => (
              <div key={name} className="sh-row">
                <span>{name}</span>
                <b>{rate.toFixed(1)}%</b>
              </div>
            ))}
          </div>
        </div>
        {props.flash ? <div className="sh-flash" data-testid="story-dashboard-flash" /> : null}
      </main>
      {props.toast !== null ? (
        <div className="sh-toast" role="status" data-testid="story-dashboard-toast">
          {props.toast}
          <X size={14} strokeWidth={2.4} />
        </div>
      ) : null}
    </div>
  );
});

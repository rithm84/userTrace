/**
 * The landing's Shopalytics fixture: the SCENARIO.md happy path (one request, one
 * Product Signal, approved_live, one PR) extended with the V3 evidence the
 * landing advertises (corroboration and frequency) and one demonstration
 * repair. Every product mock on the page reads from here so the story, the
 * comic strip and the Case File never disagree.
 */

export type SurfaceKind = "discord" | "reddit" | "appstore" | "x";

export type Corroboration = Readonly<{
  kind: SurfaceKind;
  quote: string;
  byline: string;
}>;

export type FilterChip = Readonly<{
  /** Which acceptance criterion the filter belongs to. */
  criterion: "date" | "traffic" | "demographic" | "sentiment" | "topic" | "product" | "metric";
  label: string;
}>;

export type ValidationOutcome = "validated" | "partially validated" | "not validated" | "unclear";

export const SHOPALYTICS = Object.freeze({
  client: "Shopalytics",
  account: "Dudulemon",
  user: { handle: "maya", role: "growth lead", avatarSrc: "/hero-avatars/pfp-03.png" },
  request: {
    channel: "#feedback",
    time: "Today at 9:02 AM",
    text: "I keep rebuilding the same conversion filters every Monday. Can Shopalytics let me save a view for TikTok traffic from women 25-34 on products with negative sizing reviews, then reopen it later?",
  },
  corroboration: [
    { kind: "reddit", quote: "Anyone else redoing the same conversion filters every week?", byline: "r/shopalytics" },
    { kind: "appstore", quote: "Great data, no saved views.", byline: "App Store review, two stars" },
    { kind: "x", quote: "rebuilding it weekly is my whole monday", byline: "@samops" },
  ] as readonly Corroboration[],
  frequency: { people: 4, accounts: 3, reports: 4, firstSeen: "Oct 3", lastSeen: "today" },
  filters: [
    { criterion: "date", label: "Last 30 days" },
    { criterion: "traffic", label: "TikTok" },
    { criterion: "demographic", label: "Women 25–34" },
    { criterion: "sentiment", label: "Negative reviews" },
    { criterion: "topic", label: "Sizing" },
    { criterion: "product", label: "Dresses" },
    { criterion: "metric", label: "Conversion rate" },
  ] as readonly FilterChip[],
  savedViewName: "TikTok sizing drop-off",
  gate: {
    decision: "approved_live",
    considered: ["Reproduction", "User confirmation", "Telemetry", "Scope", "Autonomy Policy"],
    scope: ["Persist saved views per account", "A control that saves the current view by name", "A list for reopening a saved view", "Restore every filter and the visualization"],
    excluded: ["Sharing", "Rename or delete", "Notifications", "A dashboard redesign"],
  },
  implementation: {
    repository: "shopalytics/web",
    branch: "utrace/saved-conversion-views",
    pullRequest: 482,
    filesTouched: 3,
    checks: ["Lint", "Typecheck", "Tests", "Build"],
    previewVersion: "9f1c2e",
    repairPreviewVersion: "b40d71",
  },
  validation: {
    /** The first Validation Attempt: one acceptance point unmet, by demonstration. */
    partial: { outcome: "partially validated" as ValidationOutcome, missingCriterion: "topic" as FilterChip["criterion"] },
    final: { outcome: "validated" as ValidationOutcome },
  },
  trace: {
    route: "/conversion",
    targets: ["filter bar", "Save view"],
    spans: ["GET /api/conversion 200", "POST /api/views 201", "GET /api/views/:id 200"],
    errors: "none",
  },
  risk: {
    blastRadius: "Conversion view state, per account.",
    assumptions: ["View names are unique per account."],
    followUps: ["Migration for the views table."],
  },
  /**
   * The Outreach Offer behind the invitation (SCENARIO.md: a 250 USD credit, then a 1,000 USD
   * invoice with 750 USD left due), in whole US dollars.
   */
  offer: {
    credit: 250,
    invoice: { number: "SHOP-1107", plan: "Growth plan", period: "October 2026", amount: 1000, due: "October 3, 2026" },
  },
});

export type ShopalyticsFixture = typeof SHOPALYTICS;

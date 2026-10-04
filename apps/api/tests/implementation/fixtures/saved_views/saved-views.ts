/**
 * Saved Conversion Views.
 *
 * The conversion filter stack lives in React state, so leaving or reloading the
 * dashboard loses it. A saved view persists one named stack for one account and
 * restores it later, including the conversion states the chart plots, so the
 * restored view shows the same series rather than a similar-looking one.
 *
 * This module is pure and storage-agnostic: it validates, upserts, finds, and
 * serializes views, and reads and writes them through the small storage surface
 * it is handed. It performs no network access and holds no React state.
 *
 * Stored content is untrusted: it survives browser sessions and can be edited by
 * hand. `parseSavedViews` therefore validates every field and refuses content it
 * cannot read, instead of silently returning a partly-populated stack.
 */

import type { Sentiment } from "@/lib/db";

export const SAVED_VIEWS_STORAGE_KEY = "shopalytics.saved-conversion-views";

export const SAVED_VIEWS_SCHEMA_VERSION = 1;

const SENTIMENTS: readonly Sentiment[] = ["positive", "neutral", "negative"];

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/u;

/** The exact filter stack a saved view restores. */
export interface SavedViewFilterStack {
  productIds: string[];
  trafficSourceIds: string[];
  demographicSegmentIds: string[];
  sentiments: Sentiment[];
  reviewTopicIds: string[];
  conversionStateIds: string[];
  startDate: string;
  endDate: string;
}

/** One saved view, owned by one account. */
export interface SavedConversionView {
  id: string;
  name: string;
  accountId: string;
  filters: SavedViewFilterStack;
  savedAt: string;
}

/** The part of `Storage` this module uses, so a test needs no browser. */
export type SavedViewStorage = Pick<Storage, "getItem" | "setItem">;

/** Stored content could not be read as saved views. */
export class SavedViewsCorruptError extends Error {
  constructor(reason: string) {
    super(`saved conversion views could not be read: ${reason}`);
    this.name = "SavedViewsCorruptError";
  }
}

/** Trims a user-supplied name and refuses an empty one. */
export function normalizeViewName(name: string): string {
  const trimmed = name.trim();
  if (trimmed.length === 0) {
    throw new Error("a saved view needs a name");
  }
  return trimmed;
}

/**
 * Adds a view, or replaces the one this account already saved under that name.
 * Saving the same name twice updates the stack rather than creating a duplicate.
 */
export function upsertSavedView(
  views: readonly SavedConversionView[],
  next: SavedConversionView,
): SavedConversionView[] {
  const name = normalizeViewName(next.name);
  const replaced = { ...next, name };
  const kept = views.filter(
    (view) =>
      view.accountId !== replaced.accountId || view.name.toLowerCase() !== name.toLowerCase(),
  );
  return [...kept, replaced].sort((left, right) => left.name.localeCompare(right.name));
}

/** Returns the view with this identifier, or undefined when there is none. */
export function findSavedView(
  views: readonly SavedConversionView[],
  id: string,
): SavedConversionView | undefined {
  return views.find((view) => view.id === id);
}

/** Returns the views this account saved, in name order. */
export function viewsForAccount(
  views: readonly SavedConversionView[],
  accountId: string,
): SavedConversionView[] {
  return views.filter((view) => view.accountId === accountId);
}

/** Serializes views for storage. */
export function serializeSavedViews(views: readonly SavedConversionView[]): string {
  return JSON.stringify({ version: SAVED_VIEWS_SCHEMA_VERSION, views });
}

/**
 * Parses stored content. `null` means nothing has been saved yet and returns an
 * empty list; anything unreadable throws.
 */
export function parseSavedViews(raw: string | null): SavedConversionView[] {
  if (raw === null) {
    return [];
  }
  let decoded: unknown;
  try {
    decoded = JSON.parse(raw);
  } catch (error) {
    throw new SavedViewsCorruptError(`stored content is not JSON (${String(error)})`);
  }
  if (!isRecord(decoded)) {
    throw new SavedViewsCorruptError("stored content is not an object");
  }
  if (decoded.version !== SAVED_VIEWS_SCHEMA_VERSION) {
    throw new SavedViewsCorruptError(`unsupported schema version ${String(decoded.version)}`);
  }
  if (!Array.isArray(decoded.views)) {
    throw new SavedViewsCorruptError("stored content carries no view list");
  }
  return decoded.views.map((view, index) => parseView(view, index));
}

/** Reads the saved views for one account out of storage. */
export function readSavedViews(
  storage: SavedViewStorage,
  accountId: string,
): SavedConversionView[] {
  return viewsForAccount(parseSavedViews(storage.getItem(SAVED_VIEWS_STORAGE_KEY)), accountId);
}

/** Writes the complete view list back to storage. */
export function writeSavedViews(
  storage: SavedViewStorage,
  views: readonly SavedConversionView[],
): void {
  storage.setItem(SAVED_VIEWS_STORAGE_KEY, serializeSavedViews(views));
}

function parseView(value: unknown, index: number): SavedConversionView {
  if (!isRecord(value)) {
    throw new SavedViewsCorruptError(`view ${index} is not an object`);
  }
  return {
    id: requireString(value.id, `view ${index} id`),
    name: requireString(value.name, `view ${index} name`),
    accountId: requireString(value.accountId, `view ${index} accountId`),
    savedAt: requireString(value.savedAt, `view ${index} savedAt`),
    filters: parseFilters(value.filters, index),
  };
}

function parseFilters(value: unknown, index: number): SavedViewFilterStack {
  if (!isRecord(value)) {
    throw new SavedViewsCorruptError(`view ${index} carries no filter stack`);
  }
  return {
    productIds: requireStrings(value.productIds, `view ${index} productIds`),
    trafficSourceIds: requireStrings(value.trafficSourceIds, `view ${index} trafficSourceIds`),
    demographicSegmentIds: requireStrings(
      value.demographicSegmentIds,
      `view ${index} demographicSegmentIds`,
    ),
    sentiments: requireSentiments(value.sentiments, `view ${index} sentiments`),
    reviewTopicIds: requireStrings(value.reviewTopicIds, `view ${index} reviewTopicIds`),
    conversionStateIds: requireStrings(
      value.conversionStateIds,
      `view ${index} conversionStateIds`,
    ),
    startDate: requireIsoDate(value.startDate, `view ${index} startDate`),
    endDate: requireIsoDate(value.endDate, `view ${index} endDate`),
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireString(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new SavedViewsCorruptError(`${label} is not a non-empty string`);
  }
  return value;
}

function requireIsoDate(value: unknown, label: string): string {
  const text = requireString(value, label);
  if (!ISO_DATE.test(text)) {
    throw new SavedViewsCorruptError(`${label} is not a yyyy-mm-dd date`);
  }
  return text;
}

function requireStrings(value: unknown, label: string): string[] {
  if (!Array.isArray(value)) {
    throw new SavedViewsCorruptError(`${label} is not an array`);
  }
  return value.map((item, position) => requireString(item, `${label}[${position}]`));
}

function requireSentiments(value: unknown, label: string): Sentiment[] {
  return requireStrings(value, label).map((item, position) => {
    const sentiment = SENTIMENTS.find((candidate) => candidate === item);
    if (sentiment === undefined) {
      throw new SavedViewsCorruptError(`${label}[${position}] is not a known sentiment`);
    }
    return sentiment;
  });
}

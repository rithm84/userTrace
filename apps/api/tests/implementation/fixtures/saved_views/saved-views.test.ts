import { describe, expect, it } from "vitest";

import {
  SAVED_VIEWS_STORAGE_KEY,
  SavedViewsCorruptError,
  findSavedView,
  normalizeViewName,
  parseSavedViews,
  readSavedViews,
  serializeSavedViews,
  upsertSavedView,
  writeSavedViews,
  type SavedConversionView,
  type SavedViewFilterStack,
  type SavedViewStorage,
} from "@/lib/dashboard/saved-views";

const ACCOUNT = "9c5f5d0a-0d6a-4a1e-9c1f-2f1f3a4b5c6d";
const OTHER_ACCOUNT = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d";

/** The canonical demo stack: TikTok traffic, women 25-34, negative sizing reviews. */
const STACK: SavedViewFilterStack = {
  productIds: [],
  trafficSourceIds: ["b1c2d3e4-0000-4000-8000-000000000001"],
  demographicSegmentIds: ["b1c2d3e4-0000-4000-8000-000000000002"],
  sentiments: ["negative"],
  reviewTopicIds: ["b1c2d3e4-0000-4000-8000-000000000003"],
  conversionStateIds: ["b1c2d3e4-0000-4000-8000-000000000004"],
  startDate: "2026-08-16",
  endDate: "2026-09-14",
};

function view(id: string, name: string, accountId = ACCOUNT): SavedConversionView {
  return { id, name, accountId, filters: STACK, savedAt: "2026-09-14T09:00:00.000Z" };
}

function memoryStorage(initial: string | null): SavedViewStorage & { value: string | null } {
  return {
    value: initial,
    getItem(key: string) {
      return key === SAVED_VIEWS_STORAGE_KEY ? this.value : null;
    },
    setItem(key: string, next: string) {
      if (key === SAVED_VIEWS_STORAGE_KEY) {
        this.value = next;
      }
    },
  };
}

describe("normalizeViewName", () => {
  it("trims a supplied name", () => {
    expect(normalizeViewName("  TikTok sizing drop-off  ")).toBe("TikTok sizing drop-off");
  });

  it("refuses an empty name", () => {
    expect(() => normalizeViewName("   ")).toThrow("a saved view needs a name");
  });
});

describe("upsertSavedView", () => {
  it("adds a view and keeps the list in name order", () => {
    const views = upsertSavedView(
      upsertSavedView([], view("v2", "TikTok sizing drop-off")),
      view("v1", "Email returns"),
    );
    expect(views.map((item) => item.name)).toEqual(["Email returns", "TikTok sizing drop-off"]);
  });

  it("replaces the same account's view with the same name rather than duplicating it", () => {
    const first = upsertSavedView([], view("v1", "TikTok sizing drop-off"));
    const second = upsertSavedView(first, view("v2", "tiktok sizing drop-off"));
    expect(second).toHaveLength(1);
    expect(second[0]!.id).toBe("v2");
  });

  it("keeps another account's view with the same name", () => {
    const first = upsertSavedView([], view("v1", "TikTok sizing drop-off"));
    const second = upsertSavedView(first, view("v2", "TikTok sizing drop-off", OTHER_ACCOUNT));
    expect(second).toHaveLength(2);
  });
});

describe("findSavedView", () => {
  it("returns the view with that identifier", () => {
    const views = upsertSavedView([], view("v1", "TikTok sizing drop-off"));
    expect(findSavedView(views, "v1")?.filters).toEqual(STACK);
    expect(findSavedView(views, "missing")).toBeUndefined();
  });
});

describe("storage round trip", () => {
  it("restores every filter and the conversion states the chart plots", () => {
    const storage = memoryStorage(null);
    writeSavedViews(storage, upsertSavedView([], view("v1", "TikTok sizing drop-off")));
    const restored = readSavedViews(storage, ACCOUNT);
    expect(restored).toHaveLength(1);
    expect(restored[0]!.filters).toEqual(STACK);
  });

  it("returns only the requested account's views", () => {
    const storage = memoryStorage(null);
    writeSavedViews(storage, [view("v1", "Mine"), view("v2", "Theirs", OTHER_ACCOUNT)]);
    expect(readSavedViews(storage, ACCOUNT).map((item) => item.id)).toEqual(["v1"]);
  });

  it("treats absent storage as no saved views", () => {
    expect(readSavedViews(memoryStorage(null), ACCOUNT)).toEqual([]);
  });
});

describe("parseSavedViews", () => {
  it("refuses content that is not JSON", () => {
    expect(() => parseSavedViews("{nope")).toThrow(SavedViewsCorruptError);
  });

  it("refuses an unsupported schema version", () => {
    expect(() => parseSavedViews(JSON.stringify({ version: 99, views: [] }))).toThrow(
      SavedViewsCorruptError,
    );
  });

  it("refuses a view whose date is not a yyyy-mm-dd string", () => {
    const broken = serializeSavedViews([
      { ...view("v1", "Broken"), filters: { ...STACK, startDate: "last monday" } },
    ]);
    expect(() => parseSavedViews(broken)).toThrow(/startDate is not a yyyy-mm-dd date/u);
  });

  it("refuses a view whose sentiment is not a known value", () => {
    const broken = JSON.parse(serializeSavedViews([view("v1", "Broken")])) as {
      views: { filters: { sentiments: string[] } }[];
    };
    broken.views[0]!.filters.sentiments = ["furious"];
    expect(() => parseSavedViews(JSON.stringify(broken))).toThrow(/not a known sentiment/u);
  });
});

"use client";

/**
 * The conversion dashboard: the surface the uTrace demo is about.
 *
 * The filter stack is React state. A Saved Conversion View persists one named
 * stack for the account and restores it later, so the recurring analysis does
 * not start by rebuilding the same filters every Monday.
 *
 * Every applied filter, the displayed series identity and each surface's
 * rendering completion are published to the uTrace chart-state channel, so the
 * demo can observe the restored state rather than infer it from an API
 * response or a spoken confirmation.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { keepPreviousData, useQueries } from "@tanstack/react-query";

import { PageHeader } from "@/components/app/PageHeader";
import {
  FilterBar,
  activeFilterCount,
  createDefaultFilterState,
  type FilterState,
} from "@/components/dashboard/FilterBar";
import { DashboardSkeleton } from "@/components/dashboard-skeleton";
import { KpiCards } from "@/components/dashboard/KpiCards";
import { SavedViewsBar } from "@/components/dashboard/SavedViewsBar";
import { ProductTable } from "@/components/dashboard/ProductTable";
import { ReviewsPanel } from "@/components/dashboard/ReviewsPanel";
import { TrendChart } from "@/components/dashboard/TrendChart";
import {
  getConversionTrend,
  getKpis,
  getProductBreakdown,
  getReviews,
  type AnalyticsFilters,
  type ConversionStateRow,
  type DashboardContext,
  type DimensionRow,
} from "@/lib/db";
import { matchDateRangePreset } from "@/lib/fixture/clock";
import {
  findSavedView,
  readSavedViews,
  upsertSavedView,
  writeSavedViews,
  type SavedConversionView,
} from "@/lib/dashboard/saved-views";
import {
  buildSeriesIdentity,
  type AppliedFilterValues,
  type SelectedDimension,
} from "@/lib/utrace/chart-state";
import { useChartStateChannel } from "@/lib/utrace/use-chart-state";

export type ConversionDimensions = Readonly<{
  products: DimensionRow[];
  trafficSources: DimensionRow[];
  segments: DimensionRow[];
  reviewTopics: DimensionRow[];
  conversionStates: ConversionStateRow[];
}>;

interface Props {
  context: DashboardContext;
  now: Date;
  dimensions: ConversionDimensions;
}

const REVIEW_LIMIT = 200;

function selectedDimensions(
  options: readonly DimensionRow[],
  selectedIds: readonly string[],
): SelectedDimension[] {
  return selectedIds.map((id) => {
    const option = options.find((candidate) => candidate.id === id);
    if (option === undefined) {
      throw new Error(`the filter selected "${id}", which is not an available option`);
    }
    return { id: option.id, name: option.name };
  });
}

export function ConversionDashboard({ context, now, dimensions }: Props) {
  const channel = useChartStateChannel();
  const defaultConversionStateIds = useMemo(
    () =>
      dimensions.conversionStates
        .filter((state) => state.key === "purchased")
        .map((state) => state.id),
    [dimensions.conversionStates],
  );
  const [filterState, setFilterState] = useState<FilterState>(() =>
    createDefaultFilterState(now, defaultConversionStateIds),
  );
  const [savedViews, setSavedViews] = useState<readonly SavedConversionView[]>([]);
  const [selectedViewId, setSelectedViewId] = useState<string | null>(null);

  // Saved views live in this browser, so they load after mount rather than
  // during server rendering, where there is no storage to read.
  useEffect(() => {
    setSavedViews(readSavedViews(window.localStorage, context.account.id));
  }, [context.account.id]);

  const saveCurrentView = useCallback(
    (name: string) => {
      const next = upsertSavedView(savedViews, {
        id: crypto.randomUUID(),
        name,
        accountId: context.account.id,
        filters: filterState,
        savedAt: new Date().toISOString(),
      });
      writeSavedViews(window.localStorage, next);
      setSavedViews(next);
      setSelectedViewId(next.find((view) => view.name === name.trim())?.id ?? null);
    },
    [context.account.id, filterState, savedViews],
  );

  const openSavedView = useCallback(
    (id: string) => {
      const view = findSavedView(savedViews, id);
      if (view === undefined) {
        throw new Error(`saved view "${id}" is no longer available`);
      }
      setFilterState({ ...view.filters });
      setSelectedViewId(view.id);
    },
    [savedViews],
  );

  const filters = useMemo<AnalyticsFilters>(
    () => ({ accountId: context.account.id, ...filterState }),
    [context.account.id, filterState],
  );

  const appliedFilters = useMemo<AppliedFilterValues>(
    () => ({
      dateRange: {
        preset: matchDateRangePreset(
          { startDate: filterState.startDate, endDate: filterState.endDate },
          now,
        ),
        startDate: filterState.startDate,
        endDate: filterState.endDate,
      },
      products: selectedDimensions(dimensions.products, filterState.productIds),
      channels: selectedDimensions(dimensions.trafficSources, filterState.trafficSourceIds),
      demographicSegments: selectedDimensions(
        dimensions.segments,
        filterState.demographicSegmentIds,
      ),
      reviewTopics: selectedDimensions(dimensions.reviewTopics, filterState.reviewTopicIds),
      sentiments: filterState.sentiments,
      conversionStates: selectedDimensions(
        dimensions.conversionStates,
        filterState.conversionStateIds,
      ),
    }),
    [dimensions, filterState, now],
  );

  const stateKeysById = useMemo(
    () => new Map(dimensions.conversionStates.map((state) => [state.id, state.key])),
    [dimensions.conversionStates],
  );

  // Keeping the previous result while a new filter stack loads means an empty
  // surface always means "no rows matched", never "still loading".
  const [kpisQuery, trendQuery, productsQuery, reviewsQuery] = useQueries({
    queries: [
      {
        queryKey: ["conversion-kpis", filters],
        queryFn: () => getKpis(filters),
        placeholderData: keepPreviousData,
      },
      {
        queryKey: ["conversion-trend", filters],
        queryFn: () => getConversionTrend(filters),
        placeholderData: keepPreviousData,
      },
      {
        queryKey: ["conversion-products", filters],
        queryFn: () => getProductBreakdown(filters),
        placeholderData: keepPreviousData,
      },
      {
        queryKey: ["conversion-reviews", filters],
        queryFn: () => getReviews(filters, REVIEW_LIMIT),
        placeholderData: keepPreviousData,
      },
    ],
  });

  const trend = useMemo(() => trendQuery.data ?? [], [trendQuery.data]);
  const products = useMemo(() => productsQuery.data ?? [], [productsQuery.data]);
  const reviews = useMemo(() => reviewsQuery.data ?? [], [reviewsQuery.data]);
  const series = useMemo(
    () => buildSeriesIdentity(appliedFilters.conversionStates, stateKeysById, trend),
    [appliedFilters.conversionStates, stateKeysById, trend],
  );

  useEffect(() => {
    channel.applyFilters(appliedFilters, new Date());
  }, [channel, appliedFilters]);

  useEffect(() => {
    if (trendQuery.data === undefined) return;
    const at = new Date();
    channel.displaySeries("conversion_trend", series, at);
    channel.completeRender(
      "conversion_trend",
      trend.length === 0 ? "empty" : "rendered",
      trend.length,
      at,
    );
  }, [channel, series, trend, trendQuery.data]);

  useEffect(() => {
    if (kpisQuery.data === undefined) return;
    channel.completeRender("conversion_kpis", "rendered", 1, new Date());
  }, [channel, kpisQuery.data]);

  useEffect(() => {
    if (productsQuery.data === undefined) return;
    channel.completeRender(
      "product_breakdown",
      products.length === 0 ? "empty" : "rendered",
      products.length,
      new Date(),
    );
  }, [channel, products, productsQuery.data]);

  useEffect(() => {
    if (reviewsQuery.data === undefined) return;
    channel.completeRender(
      "review_list",
      reviews.length === 0 ? "empty" : "rendered",
      reviews.length,
      new Date(),
    );
  }, [channel, reviews, reviewsQuery.data]);

  const failure = [kpisQuery.error, trendQuery.error, productsQuery.error, reviewsQuery.error].find(
    (error): error is Error => error instanceof Error,
  );

  return (
    <div className="flex flex-col gap-4" data-testid="conversion-dashboard">
      <PageHeader
        eyebrow={`Workspace \u00b7 ${context.account.name}`}
        title="Conversion"
        description="Funnel performance by channel, segment, review topic and conversion state."
      />

      <FilterBar
        state={filterState}
        setState={setFilterState}
        now={now}
        products={dimensions.products}
        trafficSources={dimensions.trafficSources}
        segments={dimensions.segments}
        reviewTopics={dimensions.reviewTopics}
        conversionStates={dimensions.conversionStates}
        activeCount={activeFilterCount(filterState)}
        onReset={() => {
          setFilterState(createDefaultFilterState(now, defaultConversionStateIds));
          setSelectedViewId(null);
        }}
      />

      <SavedViewsBar
        views={savedViews}
        selectedViewId={selectedViewId}
        onSave={saveCurrentView}
        onOpen={openSavedView}
      />

      {failure !== undefined && (
        <div
          role="alert"
          data-testid="conversion-error"
          className="rounded-xl border border-critical-fill/25 bg-critical-surface p-4 text-sm text-critical"
        >
          {failure.message}
        </div>
      )}

      {kpisQuery.data === undefined ? (
        <DashboardSkeleton />
      ) : (
        <>
          <KpiCards data={kpisQuery.data} seriesLabel={series.label} />

          <TrendChart data={trend} seriesLabel={series.label} seriesId={series.seriesId} />

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <ProductTable rows={products} seriesLabel={series.label} />
            <ReviewsPanel rows={reviews} />
          </div>
        </>
      )}
    </div>
  );
}

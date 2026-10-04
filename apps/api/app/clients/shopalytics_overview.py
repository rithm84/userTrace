"""The Shopalytics Product Overview corpus for the demo client.

Synthesized from the Shopalytics fixture repository and the demo journey in
`docs/SCENARIO.md`. Every section cites the sources it was derived from, and a
statement uTrace inferred rather than read is marked as inference.

This is demo client configuration, not product behaviour: the pilot synthesizes
an overview from the client's own prepared configuration, SDK annotations,
designated documents, and read-only code findings.

Contract: docs/arch-components/context-assembly.md#product-overview-lifecycle
"""

from __future__ import annotations

from typing import Final

from app.clients.overview import OverviewSection, OverviewSource, ProductOverview

__all__ = [
    "SHOPALYTICS_MANIFEST_HASH",
    "SHOPALYTICS_OVERVIEW",
]

SHOPALYTICS_MANIFEST_HASH: Final[str] = (
    "sha256:eee893377d423c2cade8778ff03e0060c75e59308f6350851bec21c08a87d51f"
)
"""The pinned Dudulemon seed manifest identity, from `fixtures/dudulemon/manifest.json`."""

_FIXTURE_REVISION: Final[str] = "dudulemon-saved-conversion-views@2026-09-14"
_SCENARIO_REVISION: Final[str] = "scenario@2026-10-03"

_SOURCES: Final[tuple[OverviewSource, ...]] = (
    OverviewSource(
        source_id="shopalytics/README.md",
        kind="repository_file",
        location="ShopalyticsStore/shopalytics:README.md",
        revision=_FIXTURE_REVISION,
    ),
    OverviewSource(
        source_id="shopalytics/src/lib/db/types.ts",
        kind="repository_file",
        location="ShopalyticsStore/shopalytics:src/lib/db/types.ts",
        revision=_FIXTURE_REVISION,
    ),
    OverviewSource(
        source_id="shopalytics/db/schema.sql",
        kind="repository_file",
        location="ShopalyticsStore/shopalytics:db/schema.sql",
        revision=_FIXTURE_REVISION,
    ),
    OverviewSource(
        source_id="shopalytics/src/lib/fixture/definition.ts",
        kind="repository_file",
        location="ShopalyticsStore/shopalytics:src/lib/fixture/definition.ts",
        revision=_FIXTURE_REVISION,
    ),
    OverviewSource(
        source_id="shopalytics/src/app",
        kind="repository_file",
        location="ShopalyticsStore/shopalytics:src/app",
        revision=_FIXTURE_REVISION,
    ),
    OverviewSource(
        source_id="shopalytics/src/lib/utrace/chart-state.ts",
        kind="sdk_annotation",
        location="ShopalyticsStore/shopalytics:src/lib/utrace/chart-state.ts",
        revision=_FIXTURE_REVISION,
    ),
    OverviewSource(
        source_id="utrace/docs/SCENARIO.md",
        kind="configuration",
        location="uTrace:docs/SCENARIO.md",
        revision=_SCENARIO_REVISION,
    ),
)

SHOPALYTICS_OVERVIEW: Final[ProductOverview] = ProductOverview(
    product_name="Shopalytics",
    purpose=OverviewSection(
        key="purpose",
        title="Purpose",
        body=(
            "Shopalytics is conversion analytics for ecommerce teams. A customer "
            "organization signs in to a workspace and analyses where sessions convert "
            "and where they drop off, sliced by product, traffic source, demographic "
            "segment, review topic, review sentiment and conversion state over a date "
            "range. The product answers questions about why a cohort stops converting, "
            "not questions about order fulfilment, inventory or payments."
        ),
        source_ids=("shopalytics/README.md", "shopalytics/src/lib/db/types.ts"),
        inferred=False,
    ),
    terminology=OverviewSection(
        key="terminology",
        title="Terminology",
        body=(
            "Account: one customer organization, such as Dudulemon. "
            "Traffic source (also called channel): where a session came from, for "
            "example TikTok, TikTok Shop, Instagram Reels, Paid search, Organic "
            "search, Email or Direct. "
            "Demographic segment: the audience cohort of a session, for example "
            "Women 25-34, Women 18-24, Women 35-44, Women 45+, Men 18-24, Men 25-34 "
            "or Unspecified. "
            "Review topic: what a customer review is about, for example Sizing, Fit "
            "consistency, Fabric quality, Shipping, Durability or Color accuracy. "
            "Sentiment: positive, neutral or negative. "
            "Conversion state: the terminal funnel state of a session cohort, one of "
            "Purchased, Checkout drop-off, Cart drop-off or Browse only. "
            "Filter stack: the complete set of filter values applied to the conversion "
            "dashboard at one moment. "
            "Conversion rate: purchases divided by sessions. "
            "State share: sessions ending in the selected conversion states divided by "
            "all sessions in the slice."
        ),
        source_ids=(
            "shopalytics/src/lib/db/types.ts",
            "shopalytics/src/lib/fixture/definition.ts",
        ),
        inferred=False,
    ),
    primary_users=OverviewSection(
        key="primary_users",
        title="Primary users",
        body=(
            "Growth, marketing and merchandising people inside one customer "
            "organization. They belong to an account, sign in to that account's "
            "workspace and see only its data. The demo account Dudulemon has a growth "
            "lead, Maya Okonkwo, who monitors recurring conversion problems by "
            "channel, audience and review topic. Shopalytics has no separate "
            "administrator role in the pilot fixture."
        ),
        source_ids=("shopalytics/db/schema.sql", "utrace/docs/SCENARIO.md"),
        inferred=False,
    ),
    workflows=OverviewSection(
        key="workflows",
        title="Workflows",
        body=(
            "The recurring workflow is conversion investigation on the conversion "
            "dashboard. A person picks a date range preset such as Last 30 days, then "
            "narrows by traffic source, demographic segment, review topic, sentiment "
            "and conversion state, and reads the resulting KPI cards, trend chart, "
            "product table and reviews panel. Comparing a filtered slice against the "
            "account-wide baseline for the same range is part of the same pass. "
            "Related workflows browse products, segments and reviews on their own "
            "pages. Rebuilding a filter stack is manual every time: the dashboard "
            "holds filter state only for the current view, so leaving or refreshing "
            "the dashboard loses it."
        ),
        source_ids=(
            "shopalytics/src/app",
            "shopalytics/README.md",
            "utrace/docs/SCENARIO.md",
        ),
        inferred=False,
    ),
    capabilities=OverviewSection(
        key="capabilities",
        title="Known capabilities and known gaps",
        body=(
            "Shopalytics can filter conversion data by date range, product, traffic "
            "source, demographic segment, review topic, sentiment and conversion "
            "state; show KPI summaries against an account-wide baseline; plot a trend "
            "series; rank products; and list the matching customer reviews. "
            "Shopalytics cannot save, name, reopen, share, rename, duplicate or delete "
            "a view of the dashboard: no saved-view capability exists anywhere in the "
            "product, and filter state does not survive navigation or a page reload. "
            "There is no alerting, no scheduled export and no collaboration workflow."
        ),
        source_ids=("shopalytics/README.md", "shopalytics/src/app"),
        inferred=False,
    ),
    entities=OverviewSection(
        key="entities",
        title="Entities",
        body=(
            "accounts (one customer organization), users (a person inside one "
            "account), products, traffic_sources, demographic_segments, review_topics, "
            "conversion_states, conversion_daily_metrics (one row per product, "
            "channel, segment, conversion state and day, carrying sessions, product "
            "views, add-to-carts, checkouts, purchases and revenue), customer_reviews "
            "and customer_review_topics. Date range, channel and segment restrict "
            "conversion rows directly; review topic and sentiment qualify products, so "
            "a product is in the slice when it has a matching review in the same date "
            "range."
        ),
        source_ids=("shopalytics/db/schema.sql", "shopalytics/src/lib/db/types.ts"),
        inferred=False,
    ),
    integrations=OverviewSection(
        key="integrations",
        title="Integrations",
        body=(
            "The application is a Next.js frontend and backend on port 3300 over "
            "Postgres 17, run under Node.js 24. Its uTrace integration points are a "
            "health route that reports database readiness and the installed dataset "
            "identity, a preview route-context check, a one-time preview handoff that "
            "exchanges a signed token for the growth lead's session, a browser "
            "bootstrap loaded before hydration in the preview profile, a server "
            "preload that owns the OpenTelemetry provider, and annotated chart state "
            "publishing applied filter values, displayed series identity and rendering "
            "completion. The production profile excludes the active uTrace runtime."
        ),
        source_ids=(
            "shopalytics/README.md",
            "shopalytics/src/lib/utrace/chart-state.ts",
        ),
        inferred=False,
    ),
    constraints=OverviewSection(
        key="constraints",
        title="Constraints",
        body=(
            "Preview runtimes hold a synthetic Dudulemon dataset, not production "
            "customer data, and third-party personal data is replaced with "
            "shape-preserving synthetic values on example.com. The fixture clock is "
            "2026-09-14T09:00:00Z, a Monday, so Last 30 days resolves to 2026-08-16 "
            "through 2026-09-14 and comparisons stay deterministic. Adjacent data is "
            "deliberate: TikTok Shop neighbours TikTok, Women 18-24 and Women 35-44 "
            "bracket Women 25-34, and Fit consistency neighbours Sizing, so an "
            "incorrectly restored filter shows visibly different rows. Because the "
            "dataset is synthetic, absolute revenue and session counts describe the "
            "fixture rather than Dudulemon's real trade (inference)."
        ),
        source_ids=("shopalytics/README.md", "utrace/docs/SCENARIO.md"),
        inferred=True,
    ),
    sources=_SOURCES,
)

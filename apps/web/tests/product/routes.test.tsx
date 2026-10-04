import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  FIXTURE_CASE_FILE_ID,
  FIXTURE_CASE_ID,
  FIXTURE_INVITATION_TOKEN,
  FIXTURE_SESSION_ID,
  fixtureCases,
} from "@/lib/api/fixture-data";

/** The gate-pending case in the fixture, which still has authority to withdraw. */
const NONTERMINAL_CASE_ID = fixtureCases.cases.filter(
  (item) => item.state === "gate_pending",
)[0].id;

class RedirectSignal extends Error {
  readonly url: string;

  constructor(url: string) {
    super(`redirect:${url}`);
    this.url = url;
  }
}

vi.mock("next/navigation", () => ({
  redirect: (url: string) => {
    throw new RedirectSignal(url);
  },
  notFound: () => {
    throw new Error("not_found");
  },
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(),
}));

const viewer = vi.hoisted(() => ({
  claims: null as Record<string, unknown> | null,
  accessToken: "access-token" as string | null,
}));

vi.mock("@/lib/supabase/server", () => {
  const supabase = {
    auth: {
      getSession: async () => ({
        data: { session: viewer.accessToken === null ? null : { access_token: viewer.accessToken } },
        error: null,
      }),
      getClaims: async () => ({
        data: viewer.claims === null ? null : { claims: viewer.claims },
        error: null,
      }),
      signOut: async () => ({ error: null }),
    },
  };
  return {
    createSupabaseReaderClient: async () => supabase,
    createSupabaseActionClient: async () => supabase,
  };
});

vi.mock("@/lib/session/realtime", () => ({
  SESSION_PROJECTION_TABLE: "session_projections",
  subscribeToSession: () => () => undefined,
}));
vi.mock("@/lib/session/voice", async () => {
  const actual = await vi.importActual<typeof import("@/lib/session/voice")>("@/lib/session/voice");
  return {
    ...actual,
    connectVoice: vi.fn().mockResolvedValue({
      connectionId: "c",
      voiceRunId: "v",
      setMicrophoneEnabled: vi.fn(),
      close: vi.fn(),
    }),
  };
});
vi.mock("@/lib/session/actions", () => ({
  refreshSessionProjection: vi.fn(),
  submitSessionPreflight: vi.fn(),
  openVoiceConnection: vi.fn(),
  sendSessionControl: vi.fn(),
  openPreviewHandoff: vi.fn(),
  fetchBindingOffer: vi.fn().mockResolvedValue({ ok: false, message: "unavailable in tests" }),
}));

function signInAsStaff(): void {
  viewer.accessToken = "access-token";
  viewer.claims = {
    sub: "staff-1",
    email: "priya@shopalytics.example",
    app_metadata: { provider: "email" },
  };
}

function signInAsParticipant(): void {
  viewer.accessToken = "access-token";
  viewer.claims = {
    sub: "participant-1",
    app_metadata: { provider: "discord" },
    user_metadata: { user_name: "maya" },
  };
}

beforeEach(() => {
  vi.stubEnv("UTRACE_CONTROL_PLANE_FIXTURES", "1");
  vi.stubEnv("NEXT_PUBLIC_SUPABASE_URL", "https://project.supabase.co");
  vi.stubEnv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", "publishable");
  vi.stubEnv("NEXT_PUBLIC_UTRACE_EXTENSION_ID", "abcdefghijklmnopabcdefghijklmnop");
  signInAsStaff();
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("client dashboard routes", () => {
  it("renders in-progress cases, Case Files with pull requests, and Outreach Offers", async () => {
    const { default: DashboardPage } = await import("@/app/(product)/dashboard/page");

    render(await DashboardPage());

    expect(screen.getByTestId("dashboard")).toBeInTheDocument();
    const cases = within(screen.getByTestId("dashboard-cases"));
    expect(cases.getByTestId(`case-card-${NONTERMINAL_CASE_ID}`)).toBeInTheDocument();
    expect(cases.getByTestId(`case-state-${NONTERMINAL_CASE_ID}`)).toHaveTextContent("Gate pending");
    // The opaque originating-user reference is resolved for staff, not shown raw.
    expect(cases.getByTestId(`case-user-${NONTERMINAL_CASE_ID}`)).toHaveTextContent("Ade Bakare");
    // The validated case is a published output, not in-progress work.
    expect(cases.queryByTestId(`case-card-${FIXTURE_CASE_ID}`)).toBeNull();

    const caseFiles = within(screen.getByTestId("dashboard-case-files"));
    expect(caseFiles.getByTestId(`case-file-link-${FIXTURE_CASE_FILE_ID}`)).toHaveAttribute(
      "href",
      `/case-files/${FIXTURE_CASE_FILE_ID}`,
    );
    expect(caseFiles.getByTestId(`case-file-pr-${FIXTURE_CASE_FILE_ID}`)).toHaveAttribute(
      "href",
      "https://github.com/ShopalyticsStore/shopalytics/pull/482",
    );

    const offers = within(screen.getByTestId("dashboard-offers"));
    expect(offers.getByTestId("offers-authorized")).toHaveTextContent("$1,000.00");
    expect(offers.getByTestId("offers-reserved")).toHaveTextContent("$250.00");
    expect(offers.getByTestId("offers-remaining")).toHaveTextContent("$250.00");
    expect(offers.getByTestId("offers-earned")).toHaveTextContent("$500.00");
    expect(offers.getAllByTestId(/^offer-applied-/)[0]).toHaveTextContent("$250.00 applied");
  });

  it("renders one case end to end and offers the stop control only while it is nonterminal", async () => {
    const { default: CasePage } = await import("@/app/(product)/dashboard/cases/[productSignalId]/page");

    render(await CasePage({ params: Promise.resolve({ productSignalId: FIXTURE_CASE_ID }) }));

    expect(screen.getByTestId("case-lifecycle-state")).toHaveTextContent("Closed validated");
    expect(screen.getByTestId("case-originating-user")).toHaveTextContent("Maya Okonkwo");
    expect(screen.getByTestId("case-originating-user")).toHaveTextContent("Dudulemon");
    expect(within(screen.getByTestId("case-gate-rows")).getByText("Approved live")).toBeInTheDocument();
    expect(screen.getByTestId("case-attempt-list").children).toHaveLength(1);
    expect(screen.getByTestId("case-preview-rows").children).toHaveLength(1);
    expect(screen.getByTestId("case-validation-rows")).toHaveTextContent("Validated");
    expect(screen.getByTestId("case-delivery-pr-link")).toHaveAttribute(
      "href",
      "https://github.com/ShopalyticsStore/shopalytics/pull/482",
    );
    expect(screen.getByTestId("case-offer-issuance")).toHaveTextContent("Issued");
    expect(screen.getByTestId("case-stop-unavailable")).toBeInTheDocument();
    expect(screen.queryByTestId("stop-case-open")).toBeNull();
    expect(screen.getByTestId("case-session-rows").children).toHaveLength(1);
  });

  it("offers the stop control on a case that still has authority to withdraw", async () => {
    const { default: CasePage } = await import("@/app/(product)/dashboard/cases/[productSignalId]/page");

    render(await CasePage({ params: Promise.resolve({ productSignalId: NONTERMINAL_CASE_ID }) }));

    expect(screen.getByTestId("stop-case-open")).toBeInTheDocument();
    expect(screen.getByTestId("case-gate-empty")).toBeInTheDocument();
  });

  it("renders the Case File with the sealed core separated from the updating projection", async () => {
    const { default: CaseFilePage } = await import("@/app/(product)/case-files/[caseFileId]/page");

    render(await CaseFilePage({ params: Promise.resolve({ caseFileId: FIXTURE_CASE_FILE_ID }) }));

    const core = screen.getByTestId("case-file-core");
    expect(core).toHaveAttribute("data-variant", "core");
    expect(screen.getByTestId("case-file-digest")).toHaveTextContent("Immutable core");
    for (const section of [
      "case-file-summary",
      "case-file-evidence",
      "case-file-trace",
      "case-file-gate",
      "case-file-implementation",
      "case-file-risk",
      "case-file-next-action",
    ]) {
      expect(within(core).getByTestId(section)).toBeInTheDocument();
    }
    expect(screen.getByTestId("case-file-pull-request-link")).toBeInTheDocument();
    // The sealed core names the participant by opaque reference; the page shows
    // the label the authenticated staff read model resolved for it.
    expect(screen.getByTestId("case-file-summary-user")).toHaveTextContent("Maya Okonkwo");
    expect(screen.getByTestId("case-file-summary-account")).toHaveTextContent("Dudulemon");
  });

  it("gates the Outreach Offers allowance on the owner role", async () => {
    const { default: SettingsPage } = await import("@/app/(product)/dashboard/settings/page");

    render(await SettingsPage());

    expect(screen.getByTestId("settings")).toHaveAttribute("data-role", "owner");
    expect(screen.getByTestId("member-list")).toBeInTheDocument();
    expect(screen.getByTestId("integration-repository")).toHaveTextContent("ShopalyticsStore/shopalytics");
    expect(screen.getByTestId("integration-status-offers")).toHaveTextContent("Enabled");
    expect(screen.getByTestId("budget-amount")).toHaveValue("1000");
    expect(screen.queryByTestId("budget-owner-only")).toBeNull();
  });
});

describe("route protection", () => {
  it("sends a signed-out visitor to staff sign-in with the path they asked for", async () => {
    viewer.accessToken = null;
    viewer.claims = null;
    const { default: DashboardPage } = await import("@/app/(product)/dashboard/page");

    await expect(DashboardPage()).rejects.toMatchObject({
      url: "/sign-in?next=%2Fdashboard",
    });
  });

  it("keeps a participant out of the dashboard and staff out of the session tab", async () => {
    signInAsParticipant();
    const { default: DashboardPage } = await import("@/app/(product)/dashboard/page");
    await expect(DashboardPage()).rejects.toMatchObject({ url: "/user-settings" });

    signInAsStaff();
    const { default: SessionPage } = await import("@/app/(product)/sessions/[sessionId]/page");
    await expect(
      SessionPage({ params: Promise.resolve({ sessionId: FIXTURE_SESSION_ID }) }),
    ).rejects.toMatchObject({ url: "/dashboard" });
  });

  it("sends a signed-out invitation visitor to Discord sign-in", async () => {
    viewer.accessToken = null;
    viewer.claims = null;
    const { default: InvitationPage } = await import("@/app/(product)/invitations/[token]/page");

    await expect(
      InvitationPage({ params: Promise.resolve({ token: FIXTURE_INVITATION_TOKEN }) }),
    ).rejects.toMatchObject({
      url: `/sign-in/participant?next=%2Finvitations%2F${FIXTURE_INVITATION_TOKEN}`,
    });
  });
});

describe("participant routes", () => {
  beforeEach(() => {
    signInAsParticipant();
  });

  it("renders the invitation claim entry", async () => {
    const { default: InvitationPage } = await import("@/app/(product)/invitations/[token]/page");

    render(await InvitationPage({ params: Promise.resolve({ token: FIXTURE_INVITATION_TOKEN }) }));

    expect(screen.getByTestId("claim-submit")).toHaveTextContent("Open my session");
  });

  it("renders the session tab from the durable projection", async () => {
    const { default: SessionPage } = await import("@/app/(product)/sessions/[sessionId]/page");

    render(await SessionPage({ params: Promise.resolve({ sessionId: FIXTURE_SESSION_ID }) }));

    expect(screen.getByTestId("session-tab")).toHaveAttribute("data-state", "interviewing");
    expect(screen.getByTestId("session-character")).toHaveAttribute("data-state", "listening");
    expect(screen.queryByTestId("product-sign-out")).toBeNull();
  });

  it("shows the participant's account: identity, open interviews and preferences", async () => {
    const { default: UserSettingsPage } = await import("@/app/(product)/user-settings/page");

    render(await UserSettingsPage());

    expect(screen.getByTestId("user-settings-identity-name")).toHaveTextContent("maya");
    const interview = screen.getByTestId(`user-settings-interview-${FIXTURE_SESSION_ID}`);
    expect(within(interview).getByTestId("user-settings-interview-open")).toHaveAttribute(
      "href",
      `/sessions/${FIXTURE_SESSION_ID}`,
    );
    expect(screen.getByTestId("user-settings-preferences-form")).toHaveAttribute("data-revision", "0");
    expect(screen.getByTestId("user-settings-visual_guidance_enabled")).toBeChecked();
  });
});

describe("sign-in routes", () => {
  it("renders staff email sign-in and the participant Discord route", async () => {
    const { default: StaffSignInPage } = await import("@/app/(product)/sign-in/page");
    const { default: ParticipantSignInPage } = await import("@/app/(product)/sign-in/participant/page");

    render(await StaffSignInPage({ searchParams: Promise.resolve({ next: "/dashboard/settings" }) }));
    expect(screen.getByTestId("staff-sign-in-email")).toHaveAttribute("type", "email");
    expect(screen.getByTestId("staff-sign-in-submit")).toHaveTextContent("Email me a code");
    expect(screen.getByTestId("staff-sign-in-participant-link")).toHaveAttribute(
      "href",
      "/sign-in/participant",
    );

    render(
      await ParticipantSignInPage({
        searchParams: Promise.resolve({ next: "/invitations/abc", reason: "Discord said no" }),
      }),
    );
    expect(screen.getByTestId("participant-sign-in-submit")).toHaveTextContent("Continue with Discord");
    expect(screen.getByTestId("participant-sign-in-reason")).toHaveTextContent("Discord said no");
  });
});

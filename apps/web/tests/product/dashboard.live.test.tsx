import { render, screen, within } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";

/**
 * The signed-in proof: the product surfaces rendered against the real control
 * plane, with a real Supabase access token, over the real database.
 *
 * Nothing here is a fixture. A staff identity signs in against the Supabase
 * project, the API resolves its `client_members` row, and the Server Components
 * render whatever the control plane returns. Only the cookie transport is
 * replaced: the token is put where `readViewer` looks for it, because a
 * headless render has no browser cookie jar.
 *
 * It is off unless `UTRACE_WEB_LIVE=1`, because it needs a running API and a
 * client with records in it. Run it with the repository settings exported:
 *
 *   set -a; source ../../.env.local; set +a
 *   UTRACE_WEB_LIVE=1 \
 *   UTRACE_API_BASE_URL=http://127.0.0.1:8150 \
 *   UTRACE_CLIENT_ID=<client uuid> \
 *   npx vitest run tests/product/dashboard.live.test.tsx
 */

const live = process.env.UTRACE_WEB_LIVE?.trim() === "1";

const viewer = vi.hoisted(() => ({ accessToken: "" }));

vi.mock("next/navigation", () => ({
  redirect: (url: string) => {
    throw new Error(`unexpected redirect to ${url}`);
  },
  notFound: () => {
    throw new Error("not_found");
  },
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/lib/supabase/server", () => {
  const supabase = {
    auth: {
      getSession: async () => ({
        data: { session: { access_token: viewer.accessToken } },
        error: null,
      }),
      getClaims: async () => ({
        data: {
          claims: {
            sub: "live-staff",
            email: process.env.UTRACE_DEMO_STAFF_EMAIL,
            app_metadata: { provider: "email" },
          },
        },
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

function required(name: string): string {
  const value = process.env[name]?.trim();
  if (value === undefined || value.length === 0) {
    throw new Error(`UTRACE_WEB_LIVE=1 needs ${name} exported.`);
  }
  return value;
}

async function signIn(): Promise<string> {
  const url = required("SUPABASE_URL").replace(/\/+$/u, "");
  const response = await fetch(`${url}/auth/v1/token?grant_type=password`, {
    method: "POST",
    headers: {
      apikey: required("SUPABASE_PUBLISHABLE_KEY"),
      "content-type": "application/json",
    },
    body: JSON.stringify({
      email: required("UTRACE_DEMO_STAFF_EMAIL"),
      password: required("UTRACE_DEMO_STAFF_PASSWORD"),
    }),
  });
  if (!response.ok) {
    throw new Error(`Supabase password sign-in failed with ${String(response.status)}.`);
  }
  const body = (await response.json()) as { access_token: string };
  return body.access_token;
}

describe.skipIf(!live)("the product surfaces against the real control plane", () => {
  beforeAll(async () => {
    process.env.UTRACE_CONTROL_PLANE_FIXTURES = "0";
    process.env.NEXT_PUBLIC_SUPABASE_URL ??= required("SUPABASE_URL");
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY ??= required("SUPABASE_PUBLISHABLE_KEY");
    process.env.NEXT_PUBLIC_UTRACE_EXTENSION_ID ??= "abcdefghijklmnopabcdefghijklmnop";
    required("UTRACE_API_BASE_URL");
    required("UTRACE_CLIENT_ID");
    viewer.accessToken = await signIn();
  });

  it("renders the dashboard from the client's own records", async () => {
    const { listCases, listCaseFiles } = await liveClient();
    const { isTerminalProductSignalState } = await import("@/lib/api/schemas");
    const cases = await listCases();
    const caseFiles = await listCaseFiles();
    const inProgress = cases.cases.filter((item) => !isTerminalProductSignalState(item.state));
    const { default: DashboardPage } = await import("@/app/(product)/dashboard/page");

    render(await DashboardPage());

    expect(screen.getByTestId("dashboard")).toBeInTheDocument();
    // Each list shows exactly what the control plane holds, or says it holds none.
    const panel = within(screen.getByTestId("dashboard-cases"));
    if (inProgress.length === 0) {
      expect(panel.getByTestId("cases-empty")).toBeInTheDocument();
    } else {
      expect(panel.getByTestId("case-list").children).toHaveLength(inProgress.length);
    }
    expect(screen.getByTestId("case-file-list").children).toHaveLength(
      caseFiles.case_files.length,
    );
    const offers = within(screen.getByTestId("dashboard-offers"));
    expect(offers.getByTestId("offers-panel")).toHaveAttribute("data-currency", "USD");
    expect(offers.getByTestId("offers-reserved")).toHaveTextContent("$");
    expect(screen.getByTestId("offer-list")).toBeInTheDocument();
  });

  it("resolves the opaque references a case names its people by", async () => {
    const { listCases, resolveReferences } = await liveClient();
    const cases = await listCases();
    const overview = cases.overviews.find((item) => item.concern !== undefined);
    if (overview === undefined) {
      throw new Error("no case in this client has a recorded concern");
    }

    const resolved = await resolveReferences([
      overview.originating_user_ref,
      overview.customer_account_ref,
    ]);

    expect(resolved).toHaveLength(2);
    for (const reference of resolved) {
      expect(reference.ref).toMatch(/^(client_user|customer_account):/u);
    }
  });

  it("renders one case end to end, with the stop control matching its authority", async () => {
    const { listCases } = await liveClient();
    const cases = await listCases();
    const { default: CasePage } = await import(
      "@/app/(product)/dashboard/cases/[productSignalId]/page"
    );
    const closed = cases.cases.find((item) => item.state === "closed_validated");
    if (closed === undefined) {
      throw new Error("no closed case to render");
    }

    render(await CasePage({ params: Promise.resolve({ productSignalId: closed.id }) }));

    expect(screen.getByTestId("case-detail")).toHaveAttribute("data-state", "closed_validated");
    expect(screen.getByTestId("case-session-rows")).toBeInTheDocument();
    expect(screen.getByTestId("case-preview-rows")).toBeInTheDocument();
    expect(screen.getByTestId("case-validation-rows")).toHaveTextContent("Validated");
    expect(screen.getByTestId("case-delivery-pr-link").getAttribute("href")).toMatch(
      /^https:\/\/github\.com\//u,
    );
    // Terminal work has no further authority to withdraw.
    expect(screen.getByTestId("case-stop-unavailable")).toBeInTheDocument();
    expect(screen.queryByTestId("stop-case-open")).toBeNull();
  });

  it("renders the sealed Case File core apart from its updating projection", async () => {
    const { listCaseFiles } = await liveClient();
    const caseFiles = await listCaseFiles();
    const published = caseFiles.case_files[0];
    if (published === undefined) {
      throw new Error("no published Case File to render");
    }
    const { default: CaseFilePage } = await import("@/app/(product)/case-files/[caseFileId]/page");

    render(await CaseFilePage({ params: Promise.resolve({ caseFileId: published.id }) }));

    const core = screen.getByTestId("case-file-core");
    expect(screen.getByTestId("case-file-digest")).toHaveTextContent("sha256:");
    expect(within(core).getByTestId("case-file-summary")).toBeInTheDocument();
    expect(within(core).getByTestId("case-file-trace")).toBeInTheDocument();
    expect(within(core).getByTestId("case-file-implementation")).toBeInTheDocument();
    // The core names nobody: the page shows the label the read model resolved.
    expect(screen.getByTestId("case-file-summary-user")).not.toHaveTextContent("client_user:");
  });

  it("renders the settings view from the active configuration release", async () => {
    const { default: SettingsPage } = await import("@/app/(product)/dashboard/settings/page");

    render(await SettingsPage());

    expect(screen.getByTestId("settings")).toHaveAttribute("data-role", "owner");
    expect(screen.getByTestId("integration-customer-surface")).toBeInTheDocument();
    expect(screen.getByTestId("integration-repository")).toBeInTheDocument();
    expect(screen.getByTestId("settings-budget-authorized")).toHaveTextContent("$");
  });
});

/** The same HTTP client the pages use, authenticated as the signed-in staff. */
async function liveClient(): Promise<{
  listCases: () => Promise<import("@/lib/api/schemas").CaseList>;
  listCaseFiles: () => Promise<import("@/lib/api/schemas").CaseFileList>;
  resolveReferences: (
    refs: readonly string[],
  ) => Promise<readonly import("@/lib/api/schemas").ResolvedReference[]>;
}> {
  const { HttpControlPlaneClient } = await import("@/lib/api/client");
  const client = new HttpControlPlaneClient({
    baseUrl: required("UTRACE_API_BASE_URL"),
    accessToken: viewer.accessToken,
    readAttempts: 1,
    fetchImplementation: fetch,
  });
  const clientId = required("UTRACE_CLIENT_ID");
  return {
    listCases: () => client.listCases(clientId),
    listCaseFiles: () => client.listCaseFiles(clientId),
    resolveReferences: (refs) => client.resolveReferences(clientId, refs),
  };
}

import type { Metadata } from "next";
import type { JSX } from "react";

import { CaseFileList } from "@/components/dashboard/CaseFileList";
import { CaseList } from "@/components/dashboard/CaseList";
import { OffersPanel } from "@/components/dashboard/OffersPanel";
import { ProductShell, STAFF_LINKS } from "@/components/product/ProductShell";
import { Panel } from "@/components/product/ui";
import {
  REFERENCE_KIND_CUSTOMER_ACCOUNT,
  isTerminalProductSignalState,
  opaqueReference,
} from "@/lib/api/schemas";
import { requireStaff } from "@/lib/auth/guards";
import { referenceLabels } from "@/lib/dashboard/references";
import { DASHBOARD_PATH } from "@/lib/routes";

export const metadata: Metadata = { title: "Dashboard — uTrace" };

export default async function DashboardPage(): Promise<JSX.Element> {
  const { viewer, clientId, client, api } = await requireStaff(DASHBOARD_PATH);
  const [cases, caseFiles, offers] = await Promise.all([
    api.listCases(clientId),
    api.listCaseFiles(clientId),
    api.getOffers(clientId),
  ]);

  const overviews = new Map(cases.overviews.map((overview) => [overview.product_signal_id, overview]));
  const concerns = new Map(
    cases.overviews.flatMap((overview) =>
      overview.concern === undefined ? [] : [[overview.product_signal_id, overview.concern.pain_point]],
    ),
  );
  const deliveries = new Map(caseFiles.deliveries.map((delivery) => [delivery.case_file_id, delivery]));

  // Every opaque reference these lists hold, resolved in one authenticated read.
  const references = await api.resolveReferences(clientId, [
    ...new Set([
      ...cases.overviews.flatMap((overview) => [
        overview.originating_user_ref,
        overview.customer_account_ref,
      ]),
      ...offers.offers.map((offer) =>
        opaqueReference(REFERENCE_KIND_CUSTOMER_ACCOUNT, offer.customer_account_id),
      ),
    ]),
  ]);
  const labels = referenceLabels(references);

  const inProgress = cases.cases.filter((item) => !isTerminalProductSignalState(item.state));

  return (
    <ProductShell main="contained" links={STAFF_LINKS} current="dashboard" viewerLabel={viewer.email}>
      <div className="pr-stack" data-testid="dashboard">
        <div className="pr-head">
          <div>
            <h1 className="pr-h1">{client.client.name}</h1>
            <p className="pr-lede">
              Customer signal in progress, the Case Files it produced, and the Outreach Offers
              backing them.
            </p>
          </div>
        </div>

        <Panel
          title="In-progress cases"
          variant="raised"
          testId="dashboard-cases"
          aside={
            <p className="pr-note">
              {inProgress.length} of {cases.cases.length} cases
            </p>
          }
        >
          <CaseList cases={inProgress} overviews={overviews} labels={labels} />
        </Panel>

        <Panel
          title="Case Files"
          variant="raised"
          testId="dashboard-case-files"
          aside={<p className="pr-note">Published outputs and their pull requests</p>}
        >
          <CaseFileList
            caseFiles={caseFiles.case_files}
            deliveries={deliveries}
            concerns={concerns}
          />
        </Panel>

        <Panel
          title="Outreach Offers"
          variant="raised"
          testId="dashboard-offers"
          aside={<p className="pr-note">Invoice credits toward {client.client.product_name} bills</p>}
        >
          <OffersPanel offers={offers} labels={labels} />
        </Panel>
      </div>
    </ProductShell>
  );
}

import type { Metadata } from "next";
import { notFound } from "next/navigation";
import type { JSX } from "react";

import { CaseDetailView } from "@/components/dashboard/CaseDetailView";
import { ProductShell, STAFF_LINKS } from "@/components/product/ProductShell";
import { isNotFound } from "@/lib/api/errors";
import { requireStaff } from "@/lib/auth/guards";
import { referenceLabels } from "@/lib/dashboard/references";
import { casePath } from "@/lib/routes";

export const metadata: Metadata = { title: "Case — uTrace" };

export default async function CasePage(
  props: Readonly<{ params: Promise<{ productSignalId: string }> }>,
): Promise<JSX.Element> {
  const { productSignalId } = await props.params;
  const { viewer, clientId, api } = await requireStaff(casePath(productSignalId));

  const detail = await api.getCase(clientId, productSignalId).catch((error: unknown) => {
    if (isNotFound(error)) {
      notFound();
    }
    throw error;
  });

  const [references, members] = await Promise.all([
    api.resolveReferences(clientId, [
      detail.overview.originating_user_ref,
      detail.overview.customer_account_ref,
    ]),
    // An audited stop names the staff membership that invoked it.
    detail.stop === undefined ? Promise.resolve([]) : api.listMembers(clientId),
  ]);

  return (
    <ProductShell main="contained" links={STAFF_LINKS} current="dashboard" viewerLabel={viewer.email}>
      <CaseDetailView
        detail={detail}
        labels={referenceLabels(references)}
        members={new Map(members.map((member) => [member.id, member]))}
      />
    </ProductShell>
  );
}

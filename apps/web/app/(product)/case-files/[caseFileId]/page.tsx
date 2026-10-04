import type { Metadata } from "next";
import { notFound } from "next/navigation";
import type { JSX } from "react";

import { CaseFileView } from "@/components/casefile/CaseFileView";
import { ProductShell, STAFF_LINKS } from "@/components/product/ProductShell";
import { isNotFound } from "@/lib/api/errors";
import { requireStaff } from "@/lib/auth/guards";
import { referenceLabels } from "@/lib/dashboard/references";
import { caseFilePath } from "@/lib/routes";

export const metadata: Metadata = { title: "Case File — uTrace" };

/** The published Case File, whose core is frozen at publication. */
export default async function CaseFilePage(
  props: Readonly<{ params: Promise<{ caseFileId: string }> }>,
): Promise<JSX.Element> {
  const { caseFileId } = await props.params;
  const { viewer, clientId, api } = await requireStaff(caseFilePath(caseFileId));

  const caseFile = await api.getCaseFile(clientId, caseFileId).catch((error: unknown) => {
    if (isNotFound(error)) {
      notFound();
    }
    throw error;
  });

  const core = caseFile.core;
  const labels = referenceLabels(
    await api.resolveReferences(clientId, [
      ...new Set([
        ...(core === undefined
          ? []
          : [core.summary.originating_user_ref, core.summary.customer_account_ref]),
      ]),
    ]),
  );

  return (
    <ProductShell main="contained" links={STAFF_LINKS} current={null} viewerLabel={viewer.email}>
      <CaseFileView caseFile={caseFile} labels={labels} />
    </ProductShell>
  );
}

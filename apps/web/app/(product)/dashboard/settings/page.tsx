import type { Metadata } from "next";
import type { JSX } from "react";

import { BudgetForm } from "@/components/dashboard/BudgetForm";
import { IntegrationList } from "@/components/dashboard/IntegrationList";
import { MemberList } from "@/components/dashboard/MemberList";
import { ProductShell, STAFF_LINKS } from "@/components/product/ProductShell";
import { EmptyState, Panel, StatTile, formatMinorUnits } from "@/components/product/ui";
import { isOwner, requireStaff } from "@/lib/auth/guards";
import { DASHBOARD_SETTINGS_PATH } from "@/lib/routes";

export const metadata: Metadata = { title: "Settings — uTrace" };

export default async function SettingsPage(): Promise<JSX.Element> {
  const { viewer, clientId, client, api } = await requireStaff(DASHBOARD_SETTINGS_PATH);
  const [members, offers, configuration] = await Promise.all([
    api.listMembers(clientId),
    api.getOffers(clientId),
    api.getConfiguration(clientId),
  ]);
  const owner = isOwner(client);
  const currency = offers.currency ?? configuration.outreach_offers.currency;

  return (
    <ProductShell main="contained" links={STAFF_LINKS} current="settings" viewerLabel={viewer.email}>
      <div className="pr-stack" data-testid="settings" data-role={client.membership.role}>
        <div className="pr-head">
          <div>
            <h1 className="pr-h1">Settings</h1>
            <p className="pr-lede">
              Membership, integrations and the Outreach Offers allowance for {client.client.name}.
            </p>
          </div>
        </div>

        <Panel
          title="Members"
          variant="raised"
          testId="settings-members"
          aside={<p className="pr-note">One owner, zero or more admins</p>}
        >
          <MemberList members={members} />
          <p className="pr-note" style={{ marginTop: 14 }}>
            The owner alone controls billing, ownership transfer, admin grants and offboarding.
            Neither role can edit, approve or resume a case.
          </p>
        </Panel>

        <Panel
          title="Integrations"
          variant="raised"
          testId="settings-integrations"
          aside={
            <p className="pr-note">
              {configuration.repository.ref}
            </p>
          }
        >
          <IntegrationList configuration={configuration} />
        </Panel>

        <Panel
          title="Outreach Offers budget"
          variant="raised"
          testId="settings-budget"
          aside={<p className="pr-note">{currency} invoice credits</p>}
        >
          {offers.currency === undefined ? (
            <EmptyState testId="settings-budget-unauthorized">
              No issuance allowance has been authorized yet.
            </EmptyState>
          ) : (
            <div className="pr-grid" style={{ marginBottom: 18 }}>
              <StatTile
                term="Authorized"
                value={formatMinorUnits(offers.authorized_minor_units, currency)}
                detail={null}
                testId="settings-budget-authorized"
              />
              <StatTile
                term="Reserved"
                value={formatMinorUnits(offers.reserved_minor_units, currency)}
                detail={null}
                testId="settings-budget-reserved"
              />
              <StatTile
                term="Remaining"
                value={formatMinorUnits(offers.remaining_minor_units, currency)}
                detail={null}
                testId="settings-budget-remaining"
              />
              <StatTile
                term="Earned"
                value={formatMinorUnits(offers.earned_minor_units, currency)}
                detail={null}
                testId="settings-budget-earned"
              />
            </div>
          )}
          <BudgetForm
            authorizedMajorUnits={Math.round(offers.authorized_minor_units / 100)}
            currency={currency}
            ownerOnlyBlocked={!owner}
          />
        </Panel>
      </div>
    </ProductShell>
  );
}

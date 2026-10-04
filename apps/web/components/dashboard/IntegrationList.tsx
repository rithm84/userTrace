import type { JSX } from "react";

import { Chip, Row, Rows } from "@/components/product/ui";
import type { ClientConfiguration } from "@/lib/api/schemas";

/**
 * What the client's active configuration release pins.
 *
 * Onboarding writes an immutable release and a Product Signal pins the one it
 * was routed under; this is the release in force now. Everything shown is a
 * recorded configuration value, not a health check: uTrace does not claim a
 * provider is reachable because a reference exists.
 */
export function IntegrationList(
  props: Readonly<{ configuration: ClientConfiguration }>,
): JSX.Element {
  const { configuration } = props;
  const surface = configuration.customer_surface;
  const repository = configuration.repository;
  const offers = configuration.outreach_offers;

  return (
    <Rows testId="integration-list">
      <Row term="Customer Surface" testId="integration-customer-surface">
        <span style={{ display: "inline-flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <Chip tone="active" testId="integration-status-customer-surface">
            Discord
          </Chip>
          <span className="pr-mono">{surface.installation_ref}</span>
          <span>
            {surface.watched_channels.map((channel) => `#${channel.name}`).join(", ")}
          </span>
        </span>
      </Row>
      <Row term="Repository" testId="integration-repository">
        <span style={{ display: "inline-flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <Chip tone="active" testId="integration-status-repository">
            GitHub
          </Chip>
          <span className="pr-mono">{repository.ref}</span>
          <span>base {repository.base_revision.slice(0, 12)}</span>
          <span>allowed {repository.permitted_paths.join(", ")}</span>
          {repository.blocked_paths.length === 0 ? null : (
            <span>blocked {repository.blocked_paths.join(", ")}</span>
          )}
        </span>
      </Row>
      <Row term="Outreach Offers" testId="integration-offers">
        <span style={{ display: "inline-flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <Chip
            tone={offers.enabled ? "active" : "waiting"}
            testId="integration-status-offers"
          >
            {offers.enabled ? "Enabled" : "Disabled"}
          </Chip>
          <span className="pr-mono">{offers.billing_account_ref}</span>
          <span>{offers.billing_use}</span>
        </span>
      </Row>
      <Row term="Data Seeding Contract" testId="integration-seeding">
        <span style={{ display: "inline-flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <span className="pr-mono">{configuration.data_seeding_manifest_hash}</span>
          <span>{configuration.environment_template_id}</span>
        </span>
      </Row>
      <Row term="Configuration release" testId="integration-release">
        <span style={{ display: "inline-flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <span>Release {configuration.release_number}</span>
          <span className="pr-mono">{configuration.digest}</span>
        </span>
      </Row>
    </Rows>
  );
}

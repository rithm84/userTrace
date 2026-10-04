import type { JSX } from "react";

import { Chip, Row, Rows, humanize } from "@/components/product/ui";
import type { ClientMember } from "@/lib/api/schemas";

/**
 * Client staff membership. One owner and zero or more admins authorize access;
 * authentication alone never does. uTrace records no separate display name for
 * staff, so a member is named by the email their identity signs in with.
 */
export function MemberList(props: Readonly<{ members: readonly ClientMember[] }>): JSX.Element {
  return (
    <Rows testId="member-list">
      {props.members.map((member) => (
        <Row key={member.id} term={member.email ?? member.id} testId={`member-${member.id}`}>
          <span style={{ display: "inline-flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <Chip
              tone={member.status === "active" ? "neutral" : "closed"}
              testId={`member-role-${member.id}`}
            >
              {humanize(member.role)}
            </Chip>
            <span className="pr-note">{humanize(member.status)}</span>
          </span>
        </Row>
      ))}
    </Rows>
  );
}

# Extension protocol schemas

The wire contracts owned by the browser-extension workstream, following the
[package conventions](../../README.md).

Three surfaces:

| Surface | Entry schema | Transport |
| --- | --- | --- |
| Control plane to extension | `inbound-frame.v1` | `WS /v1/extension/control` |
| Extension to control plane | `outbound-frame.v1` | `WS /v1/extension/control` |
| Extension to embedded browser SDK | `bridge-frame.v1` | a fresh per-document `MessageChannel` in the main world |

`installation-registration.v1` covers `POST /v1/extension/installations`.

Rules the schemas encode, from the
[browser extension contract](../../../../docs/arch-components/browser-extension.md):

- Commands address an exact registered target revision. Selectors, coordinates,
  expressions, and executable strings are not representable anywhere.
- Inbound frames except `heartbeat` carry a monotonic `sequence`; the extension
  acknowledges the highest contiguous sequence with `control_ack` and resumes
  from it with `recovery_request`.
- The control plane ends a connection with a close code the extension acts on:
  `4002` for a sequence the extension could not have reached, and `4003` after
  the handshake for an installation it holds no link for, with the reason
  `installation_unknown` or `installation_revoked`. On `4003` the extension
  drops its installation and stops reconnecting; any other close only loses the
  connection.
- `character_state`, `readiness`, `binding_state`, `control_action_result`,
  `controls_state`, and `preferences_state` each carry their own revision. Older
  revisions are ignored, and the session controls of a different session replace
  the ones the pills showed.
- Terminal command outcomes are exactly `applied`, `rejected`, `failed`,
  `stale_target`, `canceled`, and `timed_out`; every non-applied outcome carries
  a reason code.
- Scrolling a registered target into view is its own command type, never a side
  effect of a scene.

TypeScript bindings live with their consumer in
`apps/browser-extension/src/protocol/`, and the extension's own conformance test
runs the shared fixtures against them; see
[where bindings live](../../README.md#where-bindings-live).

Fixtures live in `../../fixtures/extension-protocol/`, named for the message
document they exercise rather than grouped by surface. A fixture's surface comes
from the frame union that references its document, which is how the extension's
test groups them.

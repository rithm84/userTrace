# Browser extension

[Back to uTrace architecture](../ARCHITECTURE.md)

This Manifest V3 Chrome extension presents the agent in the preview tab. It is
required during product interviews and never owns durable workflow truth. The
following diagram shows its processes and the channel between each pair:

![Extension processes in Chrome and the channel between each pair; the embedded SDK exports telemetry on its own socket.](../diagrams/svg/browser-extension.svg)

<details><summary>Diagram key</summary>

![Diagram key of the ten node roles: people and customer surfaces, uTrace control plane, reasoning models, SDKs and previews, external or blocked, validated or durable, async path, gates and checks, neutral or closed, and client product.](../diagrams/svg/_key.svg)

</details>
<p></p>

## Command boundary

The extension provides the character, controls, observation, and visual
guidance: cursor, pointing, highlighting, overlays, and explicit scrolling.
Agent clicking, typing, submission, and product navigation are not command
capabilities.

## Component boundary

- **Service worker:** installation lifecycle, account-linked device identity,
  authenticated tab bindings, the control-plane connection, routing,
  compatibility checks, recovery, and temporary checkpoints.
- **Content script:** the character, controls, agent cursor, and product
  overlays.
- **Main-world bridge:** no user interface, session credential, authorization
  decision, or durable state; it only connects the content script to the SDK.
- **Popup and settings pages:** installation and account-link status, Chrome
  permission health, protocol compatibility, connection and binding status,
  global action preferences, device-local orb settings, sign out, installation
  revocation, and troubleshooting. They cannot start an interview,
  bind an arbitrary tab, configure one client, expose transcripts or telemetry,
  or issue product commands.

## Installation, identity, permissions, and packaging

A uTrace User sets up the extension once, not per client or interview. It stays
inert until an authenticated live session supplies a valid binding offer and
page proof.

### Account linking and device identity

- Setup links the installation to the global `utrace_user_id` and registers a
  device public key. The extension keeps the non-exportable ECDSA P-256 private
  key and proves possession on every control connection and preflight; no
  bearer or binding credential is issued.
- The control plane maps the person to the client-scoped `client_user_id`; the
  extension never receives the global identity graph.
- A new browser or a reinstall requires linking again; a reinstall also gets a
  new installation identity.
- Sign-out or revocation blocks new bindings and revokes active extension
  authority. When the control connection refuses an installation no longer
  linked (revoked elsewhere or unknown), the extension drops its local record
  and device key and offers linking again.
- A uTrace User has at most one active live interview across all
  installations; a second session cannot take the extension and is rejected as
  `user_already_in_live_session`.

### Chrome permissions

- The manifest requests `storage`, `scripting`, `identity`, and `alarms`. A
  one-minute alarm, created at install and update and never cleared, restarts a
  suspended service worker to reload state from storage, without connecting or
  polling. The control connection opens only for a preflight or a session that
  needs it.
- Optional host permissions `https://*/*` and `http://*/*` are requested
  together during one-time setup; uTrace never uses `activeTab` or asks for host
  access per client, tab, or interview.
- The extension does not request `tabs`, `webNavigation`, `webRequest`,
  `cookies`, `debugger`, `userScripts`, `tabCapture`, or `offscreen`.
- `externally_connectable` lists only the session-tab origins, both in the
  production build: the uTrace-owned production origin and the local web app's
  `http://localhost:3100`. The service worker rechecks the sender's origin
  against that list.

### Supported browsers and contexts

- Chrome 137, the first release with Ed25519 in WebCrypto, is the minimum. The
  embedded SDK verifies control-plane signed envelopes as detached Ed25519 over
  a base64url JSON payload, so an earlier Chrome fails the activation gate
  rather than accept weaker proof. The extension never verifies the page proof
  it forwards.
- Only normal Chrome profiles and ordinary HTTP and HTTPS pages are supported
  preview contexts; Incognito, guest, `file://`, `chrome://`, browser-internal,
  PDF-viewer, and other extension pages are not.

### Packaging and code integrity

- Every executable dependency and interface asset is bundled: the Rive
  JavaScript and WebAssembly runtime, animation, vector, and SVG assets, bridge
  and interface code, styles, fonts, and images. No remote JavaScript or
  WebAssembly loads.
- The CSP allows packaged WebAssembly only as Rive requires and forbids ordinary
  `unsafe-eval`, inline scripts, and sandbox pages. Network connections are
  limited to uTrace authentication and control-plane HTTPS and WSS origins.
- Control-plane messages are schema-validated data that cannot carry executable
  code, arbitrary selectors, CSS, or expressions to interpret.
- Production builds ship signed through the Chrome Web Store; unpacked builds
  are development-only. Updates cannot expand permissions or data collection
  for unimplemented future features.

### Versions, updates, and removal

- New sessions accept the current and previous major extension protocol. Older
  records stay decodable, but an obsolete installation cannot start a session.
- An update clears the extension's session storage, so the new service worker
  resumes no binding, recovery checkpoint, or command from the old runtime and
  renegotiates compatibility on its next control connection. Mixed or
  incompatible contexts withdraw readiness until a clean compatible runtime
  exists; they never continue with partial control.
- Disablement or removal withdraws readiness and invalidates the active binding.
  Re-enablement requires fresh authentication and handshake.

### Participant compatibility preflight

Before an Interview Session starts, the claimed invitation runs a preflight that
verifies:

- a supported Chrome profile and version
- an account-linked compatible extension
- the required host permission for the expected preview origin
- control-plane connectivity (the extension opens its control connection to
  answer)
- compatible protocol support

Setup help may come first, but interviewing and reward-eligible participation
begin only after preflight succeeds. An unsupported corporate extension policy
or browser context, a denied required permission, or setup deadline expiry
closes as `unsupported_participant_environment` with a private explanation and
no Case File or earned reward. Passing preflight establishes no preview, SDK, or
observation readiness; the session must still complete the full runtime
handshake.

## Session anchor, binding, and handshake

### Session tab anchor

The session tab is the live interview's mandatory anchor; the user may focus
any other tab while it stays open. Each way of leaving has its own effect:

| Event | Effect |
| --- | --- |
| Session tab closed or navigated away, or **Finish later** | Ends the encounter without canceling the Product Signal. The control plane checkpoints evidence and in-flight effects, revokes the document binding, and stops voice and observation. |
| **End interview** confirmed | Cancels, revoking every nonterminal descendant in the Product Signal's cancellation scope. |
| A reload, network drop, or recoverable browser interruption | Opens the bounded reconnection window. Existing side effects and uncertain outcomes are recorded, not treated as rolled back. |
| Preview tab closed | Revokes its readiness and pending commands. Another valid same-session tab may bind during the recovery window; otherwise the normal terminal recovery rule applies. |

Live implementation after a leave follows the [implementation controller](./implementation.md#conversation-pause-and-interruption);
the case-level deadline, return, reminders, expiry, and cleanup follow
[Failure Recovery](./failure-recovery.md#bounded-return-and-terminal-expiry).

### Binding handshake

The following diagram shows how an offer and a page proof become one binding,
including a candidate rebind:

![An offer and a probed page proof yield one binding or leave the tab untouched; a candidate advance reloads the tab and rebinds.](../diagrams/svg/browser-extension-binding.svg)

- The binding offer is single-use, short-lived, and scoped to the client, user,
  session, preview runtime allocation, extension installation, and protocol
  version. The session tab delivers it only to the official extension.
- Telemetry ingestion signs the [page proof](./telemetry-and-evidence.md#page-proof)
  and mints the `document_attachment_id` inside it. The SDK runtime is not a
  claim, so no runtime identity can be asserted as authority.
- The extension decodes the payload for its own state, never as authority.
- Focusing a tab while an offer is pending triggers only a lightweight probe; a
  tab without a matching proof gets no interface, telemetry, commands, or
  product inspection.
- The service worker also probes a bound tab on every focus, which every scene
  causes, over its own short-lived channel, so a probe never displaces the
  content script's long-lived one.

### Trust and tab ownership

- A session ID, Chrome tab ID, origin, or SDK presence never establishes trust
  alone; the page proof is the authority for client and environment, so client
  teams configure no domain allowlists.
- One session has one authoritative preview-tab binding, and one tab belongs to
  at most one active session. Chrome `tabId` is a local locator, not a durable
  product identity.
- Focusing another valid tab for the same session automatically revokes the old
  binding before transferring authority. A tab of another live session cannot
  take over.

### Navigation and rebinding

- A same-document SPA transition keeps the document attachment while target
  revisions change. Reload or full navigation keeps the tab selected but
  requires a new document proof and attachment.
- Without a compatible SDK, readiness is suspended. Old documents and runtimes
  never receive commands for a replacement attachment.
- On a candidate advance, the extension reloads the bound tab because its
  document belongs to the old runtime. A rebind always names the allocation the
  session origin currently serves.

## Transport and temporary state

- Ports are accepted only when sender tab, frame, document, binding, and nonce
  match. Every boundary uses strict versioned schemas. Neither the device key
  nor a possession proof enters the content script or main world.
- A WebSocket heartbeat keeps the connection alive only while a session needs
  it. Each connection names its session when it opens.
- The socket carries binding, readiness, character state, controls, preferences
  (`preferences_update` and `preferences_state`), commands, results,
  cancellation, and recovery messages, never product telemetry.
- `chrome.storage.session` holds only the recovery checkpoint needed across
  service-worker suspension. Durable extension storage holds only installation
  identity, device-local presentation settings, protocol metadata, and a cache
  of control-plane-owned global preferences, and never becomes workflow truth.

### Control-plane push

The following diagram shows how the control plane pushes, unasked, a committed
change to the bound tab:

![A commit wakes the session's socket holder, whose projection pass pushes changed state and routes commands to the bound surface.](../diagrams/svg/browser-extension-projection.svg)

- A control-plane process changes a session's binding, readiness, character
  state, controls, or visual commands only by committing durable state.
- A projection pass sends `preferences_state`, `binding_state`, `readiness`,
  and `character_state` whose revision advanced since last sent, and
  `controls_state` when what the pills show changed. It routes pending commands
  recorded for the current binding and withdraws routed commands the control
  plane canceled. A connection that has sent nothing sends everything.
- A notification carries no state and grants no authority; the next change or
  reconnect repairs a missed one. Listening on `extension_control` requires a
  direct or session-mode pooled Postgres connection for the socket's database
  session.

## Readiness, foreground state, and recovery

### Readiness requirements

- Extension readiness is a revisioned control-plane record, not a local boolean.
  It requires an account-linked compatible installation, current host
  permission, an authenticated control-plane connection, exactly one valid
  binding, a current signed document handshake, and a connected content
  surface, page bridge, SDK, and shared runtime.
- Cursor, overlay, and scroll execution also require active, visible document
  state. Character and command-state revisions must be synchronized.
- Extension readiness feeds the session's
  [observation readiness](./interview-and-voice.md#readiness-has-two-boundaries)
  with the SDK, shared runtime, Server SDK, and trace correlation; only that
  combined revision permits product-specific probing or browser commands.

### Inactive and hidden preview tabs

- An inactive preview tab stays associated with the session. A visual command
  first brings the tab and its window to the front, as the
  [agent cursor](./interview-and-voice.md#agent-cursor) requires; if the tab is
  still inactive, the command fails as `tab_not_active` and is never queued.
- The voice interview may continue while another tab is active, but
  product-specific probing waits for the user to return.
- Legitimate correlated SDK events may continue from a hidden bound document,
  carrying visibility state so background activity is not mistaken for visible
  user interaction.
- Returning to the preview tab revalidates the document and readiness and
  projects the latest character state. Missed cursor scenes and animations are
  never replayed after navigation, pause, or rebind, and the user's own
  interaction supersedes any pending visual command.

### Loss and recovery

- Losing the extension, control-plane connection, document proof, SDK, or
  shared runtime immediately withdraws observation readiness, with the effects
  [losing readiness](./interview-and-voice.md#losing-readiness) describes, and
  begins bounded recovery. The character shows the reconnecting or failure
  state; detailed status stays in the session tab.
- Recovery requires a fresh handshake and the latest control-plane state, and
  clears old scenes. An interrupted product action without a confirmed result
  becomes `outcome_unknown` and is never retried automatically.
- Recovery has a preconfigured deadline that cannot be extended at runtime. If
  combined readiness does not return, the control plane preserves a resumable
  infrastructure blocker, checkpoints authorized work, and ends or pauses the
  encounter with user-safe messaging, never reporting user cancellation or
  accepting observation-dependent evidence from the incomplete interval.

## Character and controls

### Character

- The control plane publishes a monotonically revisioned semantic character
  state: `idle`, `listening`, `thinking`, `speaking`, `acknowledgement`,
  `concern`, `paused`, `reconnecting`, or `completion`. The session tab and the
  extension render the same packaged Rive character, ignore older revisions,
  and never infer durable session state from local animation.
- The collapsed extension character shows no status text; only the session tab
  does.
- The character is a small circle fixed to the viewport, with a device-local
  position and size shared across clients. Layout changes, scrolling, and SPA
  transitions never move it, and clamping keeps its expanded controls visible.

### Controls

Hover and drag behave as the [PRD](../PRD.md#agent-character-and-controls)
describes. Clicking the center does nothing, and pressing a pill invokes it
rather than starting a drag.

| Pill | Position | Effect |
| --- | --- | --- |
| Return to session | Above | Focuses the existing session tab. The only browser-local control, it works without the control-plane connection. |
| Mute or Unmute | Left | Sends a control-plane command to the existing voice runtime. The extension never captures microphone audio. |
| Pause or Resume | Right | Requests an authoritative session transition. |
| End interview | Below | Shows local confirmation, then requests control-plane closure. |

- Pills reflect the returned state revision rather than assuming success, and
  duplicate clicks reuse the pending command. Without the control-plane
  connection, state-changing controls fail explicitly.
- The pills show the session's own mute and pause state, pushed as
  `controls_state` on every change from either surface. A new session's state
  replaces whatever an earlier one left.

### Pause and resume

- Once pause is acknowledged, the extension clears the cursor scene and cancels
  pending visual commands. The orb stays paused with Resume, Return to session,
  and End available.
- Pause's effect on live implementation, and a spoken request for a quiet
  moment, follow the [implementation controller](./implementation.md#conversation-pause-and-interruption).
- While paused, the user may keep using the product, but uTrace neither
  observes nor acts ([runtime pause](./browser-sdk.md#runtime-readiness-and-failure-reporting)).
- Resume requires fresh readiness and a new structured browser snapshot; old
  targets, revisions, commands, and assumptions are never reused.

## Visual commands and cursor choreography

The extension owns browser-command lifecycle and visible choreography; the SDK
owns structured target resolution. Commands address an exact registered target
revision, never raw screen coordinates, arbitrary selectors, or JavaScript.

### Command envelope and routing

- Every browser command carries its command ID, client, session, binding,
  document attachment, target ID and revision where applicable, command type,
  typed arguments, authority references, expected lifecycle revision, and
  deadline.
- The dispatching process authorizes and records each command before the
  socket holder claims it once ([Control-plane push](#control-plane-push)). A
  command still unrouted when its binding is replaced or revoked is never
  routed; its cancellation records the effect as absent.
- The control plane offers and rechecks targets against the bound document's
  registry as ingestion recorded it: the stream's newest baseline snapshot plus
  every later `target.registry_change` in export order. A missing target fails
  as `unknown_target`; one at another revision fails as `stale_target`.
- Duplicate command IDs cannot repeat an effect. Rebind, pause, replacement,
  cancellation, or deadline expiry invalidates incompatible pending commands.
- Browser-command terminal outcomes are `applied`, `rejected`, `failed`,
  `stale_target`, `canceled`, or `timed_out`. A result records the actual
  binding, document, target revision, completion time, and specific reason.
  Dispatch success is separate from later telemetry about the product's
  observed effect.

### Cursor choreography

- A gesture dispatches as the word after its inline
  [mark](./interview-and-voice.md#agent-cursor) plays, and speech
  never waits for cursor arrival. Cue IDs and ordering keep an older gesture
  from replacing a newer one.
- Customer scrolling or a layout change that invalidates the target revision
  before execution cancels the visual command. uTrace never blocks customer
  input.
- The command-only agent cursor fades a few seconds after its last gesture and
  stays hidden between uses. It appears beside its first destination and glides
  on a gentle eased curve over 250 to 600 milliseconds, scaled by distance;
  reduced motion removes the animation.
- The cursor never follows, replaces, or moves the customer's physical pointer.
  Pointer movement alone does not cancel a visual scene, and the cursor surface
  stays click-through.

### Pointing and scrolling

- Pointing and highlighting never auto-scroll; scrolling into view is a separate
  explicit command. It scrolls the window by the target's measured geometry and
  calls no scroll method on product DOM, staying inside the observe-only
  boundary. A target in a product scroll container the window cannot reach
  fails with `target_not_scrolled_into_view` rather than being reported as
  shown.
- Target geometry updates through scroll, resize, and layout change. Missing,
  ambiguous, or revision-changed targets fail rather than falling back to a
  guessed element or coordinate.

### Visual scenes

- A visual scene is atomic: one agent cursor plus one or more ordered target
  gestures and retained marks. If any initial target fails validation, the scene
  is rejected. The cursor may `move`, `point`, `circle`, `trace`, `label`, and
  `clear`. Comparisons reuse the same cursor sequentially while earlier marks
  may remain.
- The cursor visibly draws circles, traces, underlines, and outlines along
  paths the extension derives from current structured target geometry; the
  model never supplies freehand coordinates.
- A scene has one lifetime and clears as a unit on explicit clear, replacement,
  expiry, cancellation, or stale target. Customer interaction with an indicated
  target does not end it early. A newer scene stops any older animation.
- An unretained mark fades a few seconds after it is drawn. Conversational
  gestures never retain marks, so they accompany speech rather than accumulate.

## Rendering and target boundaries

### Overlay host

- The bound top document holds one extension-owned host element, appended to
  the document element as a fixed, full-viewport layer with reset inherited
  styles and the maximum stacking order. A closed Shadow DOM isolates product
  and extension styles. Character, cursor, and scene layers are separate; only
  the orb and controls accept pointer input.
- The main-world bridge cannot access the interface tree. If the product removes
  the host once, the extension reinjects it cleanly; repeated removal withdraws
  readiness rather than fighting the page.
- The interface renders above ordinary product modals without altering product
  CSS or stacking contexts. As an ordinary stacked element, it still paints
  below product content the browser promotes to its own top layer.

### Frames, portals, and shadow roots

- One top-level visual surface serves targets in frames too. The shared runtime
  resolves targets to clipped top-level viewport geometry and records the owning
  document, frame, SDK runtime, and target revision.
- Portal targets resolve to their actual DOM. Open or closed shadow-root
  internals are targetable only when the client component registers them
  through the SDK; the extension never pierces an unregistered closed shadow
  root.
- Same-origin and cross-origin iframe internals require a compatible
  authenticated SDK runtime inside the owning frame, to which the top-level
  extension routes the visual command; it never uses translated raw
  coordinates. Uninstrumented frames are unsupported targets.

### Visibility checks

- A visual command requires a sufficiently visible target region in the active
  viewport. The extension's own surface is excluded from product-target
  occlusion checks.
- Immediately before a visual command, visibility, target revision, binding,
  policy, permission, and user-conflict state are rechecked. Failure prevents
  dispatch and returns a specific result such as `target_obscured` or
  `stale_target`.

## Retention, diagnostics, and cleanup

### Retention and diagnostics

- The extension may process target geometry, command arguments, character
  state, and visual-command values transiently, but never persists raw DOM, page
  text, form values, transcripts, screenshots, product telemetry, or
  visual-command values.
- Extension diagnostics hold identifiers, revisions, timestamps, command status,
  protocol version, and specific failure codes, never customer content. SDK and
  shared-runtime telemetry own product-data redaction before ingestion.

### Session end and cleanup

- Terminal session state, whichever surface ended the session, immediately
  revokes tab binding and command authority and cancels pending visual and
  scroll commands; the pushed revocation detaches the extension's surface. A
  brief completion animation may finish without authority to execute anything.
- Cleanup removes the orb, cursor, overlays, content connection, page bridge,
  and temporary recovery state. The SDK stays embedded but returns to an inert
  unbound state, and reloading the product cannot restore a revoked binding.
- Cleanup keeps the installation identity, global user preferences, device-local
  orb position and size, and supported protocol metadata, but no session content
  or workflow state.

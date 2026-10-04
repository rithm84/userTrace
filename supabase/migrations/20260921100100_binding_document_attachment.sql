-- The page proof now names the document attachment ingestion issued.
--
-- The extension protocol's page proof is an Ed25519 envelope whose claims carry
-- `document_attachment_id`: telemetry ingestion mints it when the document
-- activates. The binding adopts that identity rather than issuing a second one,
-- so the extension, the shared browser runtime, and the control plane all name
-- the same attachment. The SDK runtime id is no longer a claim, so the column
-- that recorded it goes with it.
--
-- Contract: docs/arch-components/browser-extension.md#session-anchor-binding-and-handshake

alter table public.extension_bindings
  drop column sdk_runtime_id;

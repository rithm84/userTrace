-- Narrow the session readiness record to the components something observes.
--
-- Telemetry ingestion reports three observation kinds for an Interview Session:
-- the embedded browser SDK's path, the Server SDK's span path, and the
-- browser-to-server correlation that joins them. The shared browser runtime is
-- part of the browser kind rather than a separate report, and the complete
-- probe path is the conjunction of the three, so neither is a fact of its own.
--
-- Contract: docs/arch-components/telemetry-and-evidence.md#completeness-and-readiness

alter table public.session_readiness
  drop column shared_runtime_ready,
  drop column product_probe_path_complete;

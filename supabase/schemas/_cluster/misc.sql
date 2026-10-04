SELECT cron.schedule_in_database('utrace-lease-scan', '* * * * *', 'select utrace.reclaim_expired_job_leases(), utrace.expire_overdue_jobs();', 'postgres', NULL, true);

SELECT
  cron.schedule_in_database('utrace-outbox-scan', '* * * * *', 'select utrace.enqueue_ready_outbox(500), utrace.requeue_stale_outbox(interval ''5 minutes'');', 'postgres', NULL,
  true);

SELECT cron.schedule_in_database('utrace-prepared-workspace-release-scan', '* * * * *', 'select utrace.scan_prepared_workspace_releases();', 'postgres', NULL, true);

SELECT cron.schedule_in_database('utrace-preview-pool-scan', '* * * * *', 'select utrace.scan_preview_warm_pools();', 'postgres', NULL, true);

SELECT cron.schedule_in_database('utrace-preview-teardown-scan', '* * * * *', 'select utrace.scan_preview_teardowns();', 'postgres', NULL, true);

SELECT cron.schedule_in_database('utrace-telemetry-readiness-scan', '* * * * *', 'select utrace.rescan_stale_telemetry_readiness(200);', 'postgres', NULL, true);

SELECT pgmq.create('utrace_outbox');

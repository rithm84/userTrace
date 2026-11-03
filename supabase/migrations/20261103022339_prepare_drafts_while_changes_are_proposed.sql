SET local check_function_bodies = off;

ALTER TABLE "public"."prepared_workspaces"
  DROP CONSTRAINT "prepared_workspaces_check";

ALTER TABLE "public"."prepared_workspaces"
  DROP CONSTRAINT "prepared_workspaces_state_check";

CREATE TABLE "public"."prepared_drafts" (
  "id"                        uuid                     NOT NULL,
  "client_id"                 uuid                     NOT NULL,
  "product_signal_id"         uuid                     NOT NULL,
  "interview_session_id"      uuid                     NOT NULL,
  "state"                     text                     NOT NULL,
  "proposed_scope"            jsonb                    NOT NULL,
  "criteria"                  jsonb                    NOT NULL,
  "proposal_digest"           text                     NOT NULL,
  "prepared_workspace_id"     uuid,
  "files"                     jsonb,
  "files_digest"              text,
  "patch"                     text,
  "consumed_budgets"          jsonb,
  "fast_checks"               jsonb,
  "staged_runtime_ref"        text,
  "failure_detail"            text,
  "implementation_attempt_id" uuid,
  "proposed_at"               timestamp with time zone NOT NULL DEFAULT now(),
  "settled_at"                timestamp with time zone,
  "updated_at"                timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT "prepared_drafts_adopted_check" CHECK (((state <> 'adopted'::text) OR (implementation_attempt_id IS NOT NULL))),
  CONSTRAINT "prepared_drafts_drafted_check" CHECK (((state <> ALL (ARRAY['drafted'::text, 'adopted'::text])) OR ((files IS NOT NULL) AND (files_digest IS NOT NULL) AND (patch IS
    NOT NULL)))),
  CONSTRAINT "prepared_drafts_pkey" PRIMARY KEY (id),
  CONSTRAINT "prepared_drafts_state_check" CHECK ((state = ANY (ARRAY['proposed'::text, 'drafting'::text, 'drafted'::text, 'failed'::text, 'adopted'::text, 'discarded'::text])))
);

ALTER TABLE "public"."prepared_drafts"
  ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."prepared_drafts" FROM "anon", "authenticated";

ALTER TABLE "public"."acceptance_criteria_agreements"
  ADD COLUMN "prepared_draft_id" uuid;

ALTER TABLE "public"."prepared_workspaces"
  ADD COLUMN "prepared_draft_id" uuid;

CREATE OR REPLACE FUNCTION utrace.scan_prepared_workspace_releases()
  RETURNS integer
  LANGUAGE plpgsql
  SET search_path TO ''
  AS $function$
declare
  v_row record;
  v_job_id uuid;
  v_count integer := 0;
begin
  for v_row in
    select workspaces.id, workspaces.client_id
    from public.prepared_workspaces as workspaces
    join public.product_signals as signals on signals.id = workspaces.product_signal_id
    where workspaces.state in ('preparing', 'ready', 'drafting', 'failed')
      and (
        workspaces.expires_at < now()
        or signals.state in (
          'closed_validated', 'closed_no_implementation', 'closed_blocked', 'canceled'
        )
      )
  loop
    if exists (
      select 1 from public.jobs
      where job_type = 'implementation.release_prepared_workspace'
        and concurrency_key = 'prepared_workspace_release:' || v_row.id::text
        and state in ('pending', 'ready', 'leased')
    ) then
      continue;
    end if;

    v_job_id := gen_random_uuid();
    insert into public.jobs (
      id, client_id, job_type, state, input, deadline_at, timeout_seconds, max_runs,
      resource_budget, retryable_categories, backoff_base_seconds, backoff_max_seconds,
      concurrency_key
    )
    values (
      v_job_id,
      v_row.client_id,
      'implementation.release_prepared_workspace',
      'ready',
      jsonb_build_object('prepared_workspace_id', v_row.id),
      now() + interval '1 hour',
      300,
      3,
      jsonb_build_object('workspaces', 1),
      array['timeout', 'rate_limited', 'provider_unavailable'],
      30,
      300,
      'prepared_workspace_release:' || v_row.id::text
    );
    insert into public.outbox (id, client_id, state, job_id, available_at)
    values (v_job_id, v_row.client_id, 'pending', v_job_id, now());
    v_count := v_count + 1;
  end loop;
  return v_count;
end;
$function$;

CREATE OR REPLACE FUNCTION utrace.scan_sandbox_deadlines()
  RETURNS integer
  LANGUAGE plpgsql
  SET search_path TO ''
  AS $function$
declare
  v_row record;
  v_job_id uuid;
  v_count integer := 0;
begin
  for v_row in
    select allocations.client_id
    from public.preview_runtime_allocations as allocations
    where allocations.state <> 'terminated'
      and allocations.runtime_ref is not null
      and allocations.runtime_expires_at > now()
    union
    select workspaces.client_id
    from public.prepared_workspaces as workspaces
    where workspaces.state in ('preparing', 'ready', 'drafting')
      and workspaces.expires_at > now()
  loop
    if exists (
      select 1 from public.jobs
      where job_type = 'sandboxes.extend_deadlines'
        and concurrency_key = 'sandbox_deadlines:' || v_row.client_id::text
        and state in ('pending', 'ready', 'leased')
    ) then
      continue;
    end if;
    v_job_id := gen_random_uuid();
    insert into public.jobs (
      id, client_id, job_type, state, input, deadline_at, timeout_seconds, max_runs,
      resource_budget, retryable_categories, backoff_base_seconds, backoff_max_seconds,
      concurrency_key
    )
    values (
      v_job_id,
      v_row.client_id,
      'sandboxes.extend_deadlines',
      'ready',
      '{}'::jsonb,
      now() + interval '5 minutes',
      120,
      3,
      jsonb_build_object('kind', 'sandbox_deadlines'),
      array['timeout', 'rate_limited', 'provider_unavailable'],
      10,
      60,
      'sandbox_deadlines:' || v_row.client_id::text
    );
    insert into public.outbox (id, client_id, state, job_id, available_at)
    values (v_job_id, v_row.client_id, 'pending', v_job_id, now());
    v_count := v_count + 1;
  end loop;
  return v_count;
end;
$function$;

ALTER TABLE "public"."prepared_drafts"
  ADD CONSTRAINT "prepared_drafts_client_id_fkey" FOREIGN KEY (client_id) REFERENCES public.clients(id);

ALTER TABLE "public"."prepared_drafts"
  ADD CONSTRAINT "prepared_drafts_implementation_attempt_id_fkey" FOREIGN KEY (implementation_attempt_id) REFERENCES public.implementation_attempts(id);

ALTER TABLE "public"."prepared_drafts"
  ADD CONSTRAINT "prepared_drafts_interview_session_id_fkey" FOREIGN KEY (interview_session_id) REFERENCES public.interview_sessions(id);

ALTER TABLE "public"."acceptance_criteria_agreements"
  ADD CONSTRAINT "acceptance_criteria_agreements_prepared_draft_id_fkey" FOREIGN KEY (prepared_draft_id) REFERENCES public.prepared_drafts(id);

ALTER TABLE "public"."prepared_drafts"
  ADD CONSTRAINT "prepared_drafts_prepared_workspace_id_fkey" FOREIGN KEY (prepared_workspace_id) REFERENCES public.prepared_workspaces(id);

ALTER TABLE "public"."prepared_drafts"
  ADD CONSTRAINT "prepared_drafts_product_signal_id_fkey" FOREIGN KEY (product_signal_id) REFERENCES public.product_signals(id);

ALTER TABLE "public"."prepared_workspaces"
  ADD CONSTRAINT "prepared_workspaces_check2" CHECK (((state <> 'drafting'::text) OR (prepared_draft_id IS NOT NULL)));

ALTER TABLE "public"."prepared_workspaces"
  ADD CONSTRAINT "prepared_workspaces_check" CHECK (((state <> ALL (ARRAY['ready'::text, 'drafting'::text])) OR (baseline_checks IS NOT NULL)));

ALTER TABLE "public"."prepared_workspaces"
  ADD CONSTRAINT "prepared_workspaces_state_check"
    CHECK ((state = ANY (ARRAY['preparing'::text, 'ready'::text, 'drafting'::text, 'failed'::text, 'claimed'::text, 'released'::text])));

CREATE UNIQUE INDEX prepared_drafts_one_open_per_case ON public.prepared_drafts USING btree (product_signal_id)
  WHERE (state = ANY (ARRAY['proposed'::text, 'drafting'::text, 'drafted'::text]));

REVOKE ALL ON TABLE "public"."prepared_drafts" FROM "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."prepared_drafts" TO "postgres";

GRANT DELETE, INSERT, MAINTAIN, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE ON TABLE "public"."prepared_drafts" TO "service_role";

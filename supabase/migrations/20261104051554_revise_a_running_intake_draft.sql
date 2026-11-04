-- A proposal that arrives while the intake draft is still being written leaves it
-- to finish and then revises its files, so a case can hold one open draft of each
-- kind at once.
DROP INDEX IF EXISTS public.prepared_drafts_one_open_per_case;

CREATE UNIQUE INDEX prepared_drafts_one_open_per_kind ON public.prepared_drafts USING btree (product_signal_id, kind)
  WHERE (state = ANY (ARRAY['proposed'::text, 'drafting'::text, 'drafted'::text]));

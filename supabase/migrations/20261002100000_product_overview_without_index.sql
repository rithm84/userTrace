-- The Product Overview travels whole with the contexts that need product
-- knowledge, so it no longer has a search index: the index revision, index
-- name, and indexed document count go. Activating the same overview content
-- again returns the version that already holds it, so the content digest is
-- what identifies a version within a client. Dropping the columns also drops
-- the unique constraint and the check that referenced them.
--
-- Contract: docs/arch-components/context-assembly.md#product-overview-lifecycle

alter table public.product_overview_versions
  drop column index_revision,
  drop column index_name,
  drop column document_count;

alter table public.product_overview_versions
  add constraint product_overview_versions_client_id_content_digest_key
    unique (client_id, content_digest);

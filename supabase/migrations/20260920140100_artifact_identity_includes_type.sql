-- Artifact identity is the tenant, the artifact type, and the digest together.
--
-- Two artifacts can carry identical bytes in different roles: an attempt with one
-- edit cycle produces a final diff and a revision patch that are byte-identical,
-- and both have to exist as their own evidence. Addressing by digest alone
-- collapsed them into one row, so the constraint now includes the type, matching
-- the storage path the store already writes to.
--
-- Contract: docs/arch-components/implementation.md#artifact-extraction-and-cleanup

alter table public.artifacts
  drop constraint artifacts_client_id_digest_key;

alter table public.artifacts
  add constraint artifacts_client_type_digest_key
  unique (client_id, artifact_type, digest);

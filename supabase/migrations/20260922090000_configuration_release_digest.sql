-- `configuration_releases.digest` becomes the release's pinned identity.
--
-- The column held the whole policy document, so a release pinned nothing that a
-- manifest, a dashboard, or a Case File could cite, and `policy` already carried
-- the same bytes. It now holds `sha256:<hex>` over the RFC 8785 canonical form of
-- `policy`, the canonical form every other content-addressed uTrace payload uses,
-- and a check constraint refuses any other shape.
--
-- Existing rows convert in place rather than republishing: a new release would
-- change the identity every open Product Signal pinned. The table is append-only,
-- so its immutability trigger is lifted for the one statement that rewrites the
-- column and restored immediately. The canonicalizer below reproduces exactly what
-- `app.clients.configuration.release_digest` computes, and is dropped afterwards
-- so no second canonicalization implementation survives in the schema.
--
-- Contract: docs/arch-components/onboarding.md#immutable-configuration-releases

create function utrace.rfc8785_canonical(value jsonb)
returns text
language plpgsql
immutable
as $$
declare
  parts text[] := array[]::text[];
  member text;
  element jsonb;
  amount numeric;
begin
  case jsonb_typeof(value)
    when 'object' then
      -- RFC 8785 orders members by UTF-16 code unit. Policy keys are ASCII, so the
      -- C collation's bytewise order is that order.
      for member in
        select key from jsonb_object_keys(value) as key order by key collate "C"
      loop
        parts := parts || (
          to_jsonb(member)::text || ':' || utrace.rfc8785_canonical(value -> member)
        );
      end loop;
      return '{' || array_to_string(parts, ',') || '}';
    when 'array' then
      for element in select item from jsonb_array_elements(value) as item loop
        parts := parts || utrace.rfc8785_canonical(element);
      end loop;
      return '[' || array_to_string(parts, ',') || ']';
    when 'string' then
      return to_jsonb(value #>> '{}')::text;
    when 'number' then
      amount := (value #>> '{}')::numeric;
      if amount <> trunc(amount) then
        raise exception
          'configuration policy holds the non-integer number %; this canonicalizer '
          'covers only the integers the policy model declares', amount
          using errcode = 'data_exception';
      end if;
      return trunc(amount)::bigint::text;
    when 'boolean' then
      return value::text;
    when 'null' then
      return 'null';
    else
      raise exception 'unsupported jsonb type %', jsonb_typeof(value)
        using errcode = 'data_exception';
  end case;
end;
$$;

alter table public.configuration_releases disable trigger configuration_releases_immutable;

update public.configuration_releases
set digest = 'sha256:' || encode(
      sha256(convert_to(utrace.rfc8785_canonical(policy), 'UTF8')), 'hex'
    )
where digest !~ '^sha256:[0-9a-f]{64}$';

alter table public.configuration_releases enable trigger configuration_releases_immutable;

alter table public.configuration_releases
  add constraint configuration_releases_digest_is_sha256
  check (digest ~ '^sha256:[0-9a-f]{64}$');

drop function utrace.rfc8785_canonical(jsonb);

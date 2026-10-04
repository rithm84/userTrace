-- The session tab's single-use binding offer.
--
-- The extension protocol carries the offer as an opaque base64url token, which a
-- compact JWS cannot be because it contains dots. The offer is therefore an
-- opaque random token whose digest is stored here with the exact scope it was
-- minted for: client, originating user, session, preview runtime allocation,
-- extension installation, and protocol version.
--
-- Contract: docs/arch-components/browser-extension.md#session-anchor-binding-and-handshake

create table public.extension_binding_offers (
  id uuid primary key,
  client_id uuid not null references public.clients (id),
  interview_session_id uuid not null references public.interview_sessions (id),
  client_user_id uuid not null references public.client_users (id),
  extension_installation_id uuid not null
    references public.extension_installations (id),
  preview_runtime_allocation_id uuid not null
    references public.preview_runtime_allocations (id),
  protocol_version text not null,
  offer_digest text not null unique,
  expires_at timestamptz not null,
  consumed_at timestamptz,
  consumed_by_binding_id uuid references public.extension_bindings (id),
  created_at timestamptz not null default now(),
  schema_version smallint not null default 1,
  constraint extension_binding_offers_consumed_together
    check ((consumed_at is null) = (consumed_by_binding_id is null))
);
create index extension_binding_offers_session_idx
  on public.extension_binding_offers (interview_session_id, expires_at desc);

alter table public.extension_bindings
  add constraint extension_bindings_offer_fk
  foreign key (offer_token_id) references public.extension_binding_offers (id);

alter table public.extension_binding_offers enable row level security;
revoke all on table public.extension_binding_offers from anon, authenticated;

-- Provider references also belong to a client and to a client-scoped user: the
-- Discord guild and its channels belong to the client, the authorized GitHub
-- repository and the mapped Stripe Customer belong to the client, and a Discord
-- account observed in that guild belongs to one `client_users` row.
--
-- Adding enum values is expand-only. The new values cannot be used until this
-- migration commits, which is why they are alone in their own file.
--
-- Contract: docs/arch-components/identity-and-access.md#identity-and-tenancy-model

alter type public.aggregate_kind add value if not exists 'client';
alter type public.aggregate_kind add value if not exists 'client_user';

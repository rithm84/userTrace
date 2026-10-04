-- The mapped Stripe Customer belongs to a customer organization, not to the
-- client as a whole: one client's billing account holds one Customer per
-- customer account.
--
-- Adding enum values is expand-only, and a new value cannot be used until this
-- migration commits, which is why it is alone in its own file.
--
-- Contract: docs/arch-components/outreach-offers.md#billing-account-and-customer-mapping

alter type public.aggregate_kind add value if not exists 'customer_account';

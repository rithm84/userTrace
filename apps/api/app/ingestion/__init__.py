"""Telemetry ingestion: the trust boundary between client-installed producers and
uTrace evidence.

It accepts already-redacted source records, verifies them, normalizes them
without inventing meaning, joins the browser and server halves of product
operations, and exposes bounded evidence views to the control plane.
"""

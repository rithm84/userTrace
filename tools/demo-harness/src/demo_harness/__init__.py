"""The Shopalytics demo harness.

Prepares, drives, observes, and verifies the journey in ``docs/SCENARIO.md``
against a target control plane. The harness is an API client: it authenticates
as the demo staff user through Supabase Auth and reads and writes only through
the control plane's own routes. The one exception is ``reset-target``, which has
no delete routes to use and therefore states the SQL path it takes.
"""

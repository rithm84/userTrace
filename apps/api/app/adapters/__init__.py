"""Adapters that implement another area's port on top of ingestion and evidence.

The preview area asks whether a prepared runtime's Server SDK activated and
whether the browser, server, and correlation paths are ready. The validation area
asks for the promoted evidence one acceptance criterion cites. Neither can see
ingestion's storage, and ingestion does not reach into theirs: these adapters are
the two seams between them, and they are read-only over durable state.
"""

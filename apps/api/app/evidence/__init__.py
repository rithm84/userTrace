"""Evidence windows, promotion, and the typed read-only query surface.

Normalized telemetry is an observation source, not automatically a confirmed
Product Signal claim. A rolling window may advance within the active session; a
decision or output freezes a new immutable window, and later data requires a new
window rather than altering the earlier one.
"""

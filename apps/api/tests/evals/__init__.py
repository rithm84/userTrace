"""Evaluations that measure a design choice rather than check behavior.

They live beside the tests so they can reuse the fixtures and the live call harness,
but they are not part of the test suite: an evaluation's numbers go into
`docs/arch-components/benchmarks.md`, not into an assertion.
"""

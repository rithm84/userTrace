"""The execution budget ledger one Implementation Attempt run spends against.

Elapsed time, agent turns and spend, process executions, artifact bytes, and
correction cycles are pinned by the Product Signal's policy and narrowed by the
gate decision. A technical retry or a check-fix cycle inside the attempt consumes
no revision, but it still consumes these, and resuming after a pause or a repair
preserves everything already spent.

Every limit refuses loudly. There is no soft mode: a run that would exceed a
budget stops with `BudgetExhaustedError` naming the budget, what it consumed, and
its allowance, and the attempt ends `budget_exhausted`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.envelope import JsonValue
from app.errors import BudgetExhaustedError
from app.gate.scope import ResourceBudgets

__all__ = ["BudgetLedger", "ConsumedBudgets"]


@dataclass(frozen=True, slots=True)
class ConsumedBudgets:
    """What one attempt has spent so far, carried across runs and checkpoints."""

    elapsed_seconds: int
    agent_turns: int
    agent_cost_cents: int
    process_executions: int
    artifact_bytes: int
    correction_cycles: int

    @classmethod
    def empty(cls) -> ConsumedBudgets:
        """A fresh attempt that has spent nothing."""
        return cls(
            elapsed_seconds=0,
            agent_turns=0,
            agent_cost_cents=0,
            process_executions=0,
            artifact_bytes=0,
            correction_cycles=0,
        )

    @classmethod
    def from_column(cls, stored: Mapping[str, object]) -> ConsumedBudgets:
        """Rebuild the ledger's starting point from the attempt's stored column."""
        return cls(
            elapsed_seconds=_as_int(stored, "elapsed_seconds"),
            agent_turns=_as_int(stored, "agent_turns"),
            agent_cost_cents=_as_int(stored, "agent_cost_cents"),
            process_executions=_as_int(stored, "process_executions"),
            artifact_bytes=_as_int(stored, "artifact_bytes"),
            correction_cycles=_as_int(stored, "correction_cycles"),
        )

    def as_column(self) -> dict[str, JsonValue]:
        """Render the ledger for the attempt's `consumed_budgets` column."""
        return {
            "elapsed_seconds": self.elapsed_seconds,
            "agent_turns": self.agent_turns,
            "agent_cost_cents": self.agent_cost_cents,
            "process_executions": self.process_executions,
            "artifact_bytes": self.artifact_bytes,
            "correction_cycles": self.correction_cycles,
        }


def _as_int(stored: Mapping[str, object], key: str) -> int:
    value = stored.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"consumed budget '{key}' is {type(value).__name__}, not int")
    return value


class BudgetLedger:
    """Tracks one run's consumption against the attempt's pinned allowances."""

    def __init__(
        self, *, budgets: ResourceBudgets, already_consumed: ConsumedBudgets, started_at: datetime
    ) -> None:
        self._budgets = budgets
        self._base = already_consumed
        self._started_at = started_at
        self._agent_turns = 0
        self._agent_cost_cents = 0
        self._processes = 0
        self._artifact_bytes = 0
        self._cycles = 0

    @property
    def deadline(self) -> datetime:
        """When this attempt's elapsed-time allowance runs out."""
        remaining = self._budgets.elapsed_seconds - self._base.elapsed_seconds
        return self._started_at + timedelta(seconds=max(remaining, 0))

    def consumed(self) -> ConsumedBudgets:
        """The attempt's total consumption, including everything earlier runs spent."""
        return ConsumedBudgets(
            elapsed_seconds=self._base.elapsed_seconds + self._elapsed_seconds(),
            agent_turns=self._base.agent_turns + self._agent_turns,
            agent_cost_cents=self._base.agent_cost_cents + self._agent_cost_cents,
            process_executions=self._base.process_executions + self._processes,
            artifact_bytes=self._base.artifact_bytes + self._artifact_bytes,
            correction_cycles=self._base.correction_cycles + self._cycles,
        )

    def check_elapsed(self) -> None:
        """Refuse the next step when the elapsed-time allowance is spent."""
        total = self._base.elapsed_seconds + self._elapsed_seconds()
        if total >= self._budgets.elapsed_seconds:
            raise BudgetExhaustedError(
                budget="elapsed_seconds",
                consumed=total,
                allowance=self._budgets.elapsed_seconds,
            )

    def remaining_agent_turns(self) -> int:
        """Turns the next authoring cycle may take; the harness's `max_turns`."""
        return self._budgets.agent_turns - (self._base.agent_turns + self._agent_turns)

    def remaining_agent_cost_cents(self) -> int:
        """Spend the next authoring cycle may incur; the harness's `max_budget_usd`."""
        return self._budgets.agent_cost_cents - (
            self._base.agent_cost_cents + self._agent_cost_cents
        )

    def consume_agent_run(self, *, turns: int, cost_cents: int) -> None:
        """Charge one authoring cycle's turns and spend, as the harness reported them."""
        self._agent_turns += turns
        self._agent_cost_cents += cost_cents
        for budget, total, allowance in (
            ("agent_turns", self._base.agent_turns + self._agent_turns, self._budgets.agent_turns),
            (
                "agent_cost_cents",
                self._base.agent_cost_cents + self._agent_cost_cents,
                self._budgets.agent_cost_cents,
            ),
        ):
            if total > allowance:
                raise BudgetExhaustedError(budget=budget, consumed=total, allowance=allowance)

    def consume_process_execution(self) -> None:
        """Charge one process execution before it runs."""
        self._processes += 1
        total = self._base.process_executions + self._processes
        if total > self._budgets.process_executions:
            raise BudgetExhaustedError(
                budget="process_executions",
                consumed=total,
                allowance=self._budgets.process_executions,
            )
        self.check_elapsed()

    def consume_artifact_bytes(self, byte_count: int) -> None:
        """Charge bytes written into the workspace or extracted from it."""
        self._artifact_bytes += byte_count
        total = self._base.artifact_bytes + self._artifact_bytes
        if total > self._budgets.artifact_bytes:
            raise BudgetExhaustedError(
                budget="artifact_bytes",
                consumed=total,
                allowance=self._budgets.artifact_bytes,
            )

    def consume_correction_cycle(self) -> None:
        """Charge one correction cycle. Cycles consume no revision, only budget."""
        self._cycles += 1
        total = self._base.correction_cycles + self._cycles
        if total > self._budgets.correction_cycles:
            raise BudgetExhaustedError(
                budget="correction_cycles",
                consumed=total,
                allowance=self._budgets.correction_cycles,
            )
        self.check_elapsed()

    def _elapsed_seconds(self) -> int:
        return int((datetime.now(tz=UTC) - self._started_at).total_seconds())

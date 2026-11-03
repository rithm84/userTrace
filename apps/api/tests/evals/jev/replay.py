"""Replay the labeled set through Jev, Sol, and the rules, and score them.

    uv run python -m tests.evals.jev.replay --repeats 3 --out ../../tmp/jev-eval/replay.json
    uv run python -m tests.evals.jev.replay --score ../../tmp/jev-eval/replay.json

Keys come from the repository's `.env.local` (`VERCEL_AI_GATEWAY_API_KEY`,
`OPENAI_API_KEY`); nothing touches a database. Each judge answers each moment
`--repeats` times, one request at a time per judge so latency is not inflated by
queueing, with Jev and Sol running side by side. Raw verdicts are written to `--out`
and scored from there, so a scoring change never needs new calls.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import statistics
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import structlog
from openai import AsyncOpenAI

from app.connectors.typesafe_jev import JevClient
from tests.conftest import _load_env_file
from tests.evals.jev import judges
from tests.evals.jev.build_replay_set import REPLAY_SET

_KINDS: Final = ("acknowledgements", "checks", "classifications")
_JUDGES: Final[Mapping[str, tuple[str, ...]]] = {
    "acknowledgements": ("jev", "sol_judge", "sol_agent", "rule_completion", "rule_completion_awaited"),
    "checks": ("jev", "sol_judge", "rule_demo"),
    "classifications": ("jev", "sol_judge", "rule_initial_flow"),
}
_MODEL_JUDGES: Final = {"jev": "jev", "sol_judge": "sol", "sol_agent": "sol"}


async def _verdict(
    judge: str, kind: str, moment: Mapping[str, Any], *, jev: JevClient, openai: AsyncOpenAI
) -> judges.Verdict:
    if judge == "jev":
        call = {
            "acknowledgements": judges.jev_acknowledgement,
            "checks": judges.jev_checks,
            "classifications": judges.jev_classification,
        }[kind]
        return await call(jev, moment)
    if judge == "sol_judge":
        return await judges.sol_judge(openai, kind, moment)
    if judge == "sol_agent":
        return await judges.sol_agent_acknowledgement(openai, moment)
    if judge == "rule_completion":
        return judges.rule_acknowledgement(moment, conversation=False)
    if judge == "rule_completion_awaited":
        return judges.rule_acknowledgement(moment, conversation=True)
    if judge == "rule_demo":
        return judges.rule_checks(moment)
    if judge == "rule_initial_flow":
        # No classification call: the gate keeps the flow intake assigned.
        return judges.Verdict(choices={}, decided={"flow": "feature_request_flow"}, seconds=0.0)
    raise ValueError(judge)


async def run(
    replay_set: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    kinds: Iterable[str],
    judge_names: Iterable[str] | None,
    repeats: int,
) -> list[dict[str, Any]]:
    _load_env_file()
    # The connectors log every call; the replay reports its own progress.
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(30))
    jev_client = JevClient(
        api_key=os.environ["VERCEL_AI_GATEWAY_API_KEY"],
        model_id=os.environ.get("UTRACE_JEV_MODEL_ID") or "typesafe-ai/jev",
        timeout_seconds=30.0,
    )
    openai = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=2)
    providers = {"jev": asyncio.Semaphore(1), "sol": asyncio.Semaphore(1)}
    results: list[dict[str, Any]] = []

    async def one(judge: str, kind: str, moment: Mapping[str, Any], repeat: int) -> None:
        provider = _MODEL_JUDGES.get(judge)
        try:
            if provider is None:
                verdict = await _verdict(judge, kind, moment, jev=jev_client, openai=openai)
            else:
                async with providers[provider]:
                    verdict = await _verdict(judge, kind, moment, jev=jev_client, openai=openai)
            record: dict[str, Any] = dataclasses.asdict(verdict)
        except Exception as error:  # noqa: BLE001 - a failed call is a result, not a crash
            record = {"error": f"{type(error).__name__}: {error}"}
        results.append({"kind": kind, "judge": judge, "id": moment["id"], "repeat": repeat, **record})
        done = len(results)
        if done % 25 == 0:
            print(f"  {done} verdicts", flush=True)

    tasks: list[Awaitable[None]] = []
    async with jev_client, openai:
        for kind in kinds:
            names = [name for name in _JUDGES[kind] if judge_names is None or name in judge_names]
            for name in names:
                for moment in replay_set[kind]:
                    for repeat in range(1 if name.startswith("rule") else repeats):
                        tasks.append(one(name, kind, moment, repeat))
        await asyncio.gather(*tasks)
    return results


# --- Scoring -------------------------------------------------------------------


def _percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return float("nan")
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


def _ratio(numerator: int, denominator: int) -> str:
    return f"{numerator}/{denominator}" + (f" ({numerator / denominator:.0%})" if denominator else "")


def _usage(rows: Sequence[Mapping[str, Any]]) -> str:
    model_rows = [row for row in rows if row.get("seconds")]
    if not model_rows:
        return "rule"
    seconds = [row["seconds"] for row in model_rows]
    tokens_in = statistics.mean(row["input_tokens"] for row in model_rows)
    tokens_out = statistics.mean(row["output_tokens"] for row in model_rows)
    cost = statistics.mean(row.get("cost_usd", 0.0) for row in model_rows)
    return (
        f"p50 {_percentile(seconds, 0.5):.2f} s, p90 {_percentile(seconds, 0.9):.2f} s; "
        f"tokens in {tokens_in:.0f}, out {tokens_out:.0f}"
        + (f"; USD {cost:.4f} per call" if cost else "")
    )


def _consistency(rows: Sequence[Mapping[str, Any]], decide: Callable[[Mapping[str, Any]], str]) -> str:
    by_moment: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        by_moment[row["id"]].add(decide(row))
    stable = sum(1 for decisions in by_moment.values() if len(decisions) == 1)
    return _ratio(stable, len(by_moment))


def score_acknowledgements(results: Sequence[Mapping[str, Any]], items: Sequence[Mapping[str, Any]]) -> None:
    labels = {item["id"]: item for item in items}
    print("\n## Acknowledgements")
    print(
        "| Judge | Moments | Precision | Recall | Wrong acknowledgements | Missed | "
        "Precision, recall without ambiguous | Same answer every repeat | Latency and usage |"
    )
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for judge in _JUDGES["acknowledgements"]:
        rows = [r for r in results if r["kind"] == "acknowledgements" and r["judge"] == judge and "error" not in r]
        if not rows:
            continue
        line = []
        for skip_ambiguous in (False, True):
            counts: Counter[str] = Counter()
            for row in rows:
                item = labels[row["id"]]
                if skip_ambiguous and item["ambiguous"]:
                    continue
                predicted = row["decided"]["acknowledge"] == "acknowledge"
                actual = item["label"] == "acknowledge"
                counts[("t" if predicted == actual else "f") + ("p" if predicted else "n")] += 1
            precision = _ratio(counts["tp"], counts["tp"] + counts["fp"])
            recall = _ratio(counts["tp"], counts["tp"] + counts["fn"])
            line.append((precision, recall, counts))
        (p, r, c), (p2, r2, _) = line
        errors = sum(1 for row in results if row["judge"] == judge and row["kind"] == "acknowledgements" and "error" in row)
        print(
            f"| {judge} | {len({row['id'] for row in rows})} × {len(rows) // len({row['id'] for row in rows})} "
            f"| {p} | {r} | {c['fp']} | {c['fn']} | {p2}, {r2} "
            f"| {_consistency(rows, lambda row: row['decided']['acknowledge'])} | {_usage(rows)}"
            + (f"; {errors} failed" if errors else "")
            + " |"
        )
    _misses("acknowledgements", results, labels, lambda row, item: [
        ("acknowledge", item["label"], row["decided"]["acknowledge"], row.get("said"))
    ] if row["decided"]["acknowledge"] != item["label"] else [])


def score_checks(results: Sequence[Mapping[str, Any]], items: Sequence[Mapping[str, Any]]) -> None:
    labels = {item["id"]: item for item in items}
    print("\n## Checks shown")
    print(
        "| Judge | Batches | Shown: precision | Shown: recall | Contradicted: precision | "
        "Contradicted: recall | Reported shown but not shown (without ambiguous) | Shown, not reported | "
        "Shown recall without ambiguous | Same answer every repeat | Latency and usage |"
    )
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for judge in _JUDGES["checks"]:
        rows = [r for r in results if r["kind"] == "checks" and r["judge"] == judge and "error" not in r]
        if not rows:
            continue
        counts: Counter[str] = Counter()
        for row in rows:
            item = labels[row["id"]]
            for check, label in item["labels"].items():
                decided = row["decided"].get(check, "none")
                ambiguous = check in item["ambiguous"]
                for name in ("shown", "contradicted"):
                    if decided == name and label == name:
                        counts[f"{name}_tp"] += 1
                    elif decided == name:
                        counts[f"{name}_fp"] += 1
                    elif label == name:
                        counts[f"{name}_fn"] += 1
                if not ambiguous and label == "shown":
                    counts["clear_shown"] += 1
                    counts["clear_shown_hit"] += decided == "shown"
                if decided == "shown" and label != "shown":
                    counts["harmful"] += 1
                    counts["harmful_clear"] += not ambiguous
                if label == "shown" and decided != "shown":
                    counts["missed"] += 1
        batches = len({row["id"] for row in rows})
        print(
            f"| {judge} | {batches} × {len(rows) // batches} "
            f"| {_ratio(counts['shown_tp'], counts['shown_tp'] + counts['shown_fp'])} "
            f"| {_ratio(counts['shown_tp'], counts['shown_tp'] + counts['shown_fn'])} "
            f"| {_ratio(counts['contradicted_tp'], counts['contradicted_tp'] + counts['contradicted_fp'])} "
            f"| {_ratio(counts['contradicted_tp'], counts['contradicted_tp'] + counts['contradicted_fn'])} "
            f"| {counts['harmful']} ({counts['harmful_clear']}) | {counts['missed']} "
            f"| {_ratio(counts['clear_shown_hit'], counts['clear_shown'])} "
            f"| {_consistency(rows, lambda row: json.dumps(row['decided'], sort_keys=True))} | {_usage(rows)} |"
        )

    def wrong(row: Mapping[str, Any], item: Mapping[str, Any]) -> list[tuple[str, str, str, str | None]]:
        mismatches = []
        for check, label in item["labels"].items():
            decided = row["decided"].get(check, "none")
            if (decided == "none") != (label == "not_shown") or (decided != "none" and decided != label):
                mismatches.append((item["checks"][check][:60], label, decided, None))
        return mismatches

    _misses("checks", results, labels, wrong)


def score_classifications(results: Sequence[Mapping[str, Any]], items: Sequence[Mapping[str, Any]]) -> None:
    labels = {item["id"]: item for item in items}
    print("\n## Flow classification")
    print("| Judge | Interviews | Correct | Latency and usage |")
    print("| --- | --- | --- | --- |")
    for judge in _JUDGES["classifications"]:
        rows = [r for r in results if r["kind"] == "classifications" and r["judge"] == judge and "error" not in r]
        if not rows:
            continue
        correct = sum(
            1
            for row in rows
            if row["decided"]["flow"]
            == ("unresolved" if labels[row["id"]]["label"] == "unclear" else labels[row["id"]]["label"])
        )
        batches = len({row["id"] for row in rows})
        print(f"| {judge} | {batches} × {len(rows) // batches} | {_ratio(correct, len(rows))} | {_usage(rows)} |")
    _misses(
        "classifications",
        results,
        labels,
        lambda row, item: [("flow", item["label"], row["decided"]["flow"], None)]
        if row["decided"]["flow"] != ("unresolved" if item["label"] == "unclear" else item["label"])
        else [],
    )


def _misses(
    kind: str,
    results: Sequence[Mapping[str, Any]],
    labels: Mapping[str, Mapping[str, Any]],
    wrong: Callable[[Mapping[str, Any], Mapping[str, Any]], list[tuple[str, str, str, str | None]]],
) -> None:
    print(f"\nWrong answers ({kind}):")
    tally: Counter[tuple[str, str, str, str, str]] = Counter()
    said: dict[tuple[str, str, str, str, str], str] = {}
    for row in results:
        if row["kind"] != kind or "error" in row:
            continue
        for what, label, decided, words in wrong(row, labels[row["id"]]):
            key = (row["judge"], row["id"], what, label, decided)
            tally[key] += 1
            if words:
                said[key] = words
    for (judge, moment, what, label, decided), count in sorted(tally.items()):
        extra = f' said "{said[(judge, moment, what, label, decided)]}"' if (judge, moment, what, label, decided) in said else ""
        print(f"  {judge:24} {moment:44} {what:62} label {label:12} got {decided:12} ×{count}{extra}")


def fidelity(results: Sequence[Mapping[str, Any]], replay_set: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
    """How often replayed Jev reached the decision Jev reached at the time."""
    print("\n## Rebuild fidelity: replayed Jev against its recorded answers")
    for kind in _KINDS:
        recorded = {item["id"]: item["recorded"] for item in replay_set[kind] if item.get("recorded")}
        same = total = 0
        for row in results:
            if row["kind"] != kind or row["judge"] != "jev" or row["id"] not in recorded or "error" in row:
                continue
            answer = recorded[row["id"]]
            if kind == "checks":
                for check, choice in row["choices"].items():
                    total += 1
                    same += answer["choices"].get(f"check_shown::{check}") == choice
            else:
                total += 1
                same += next(iter(answer["choices"].values())) == next(iter(row["choices"].values()))
        if total:
            print(f"- {kind}: same top choice {_ratio(same, total)}")


def score(results: Sequence[Mapping[str, Any]], replay_set: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
    kinds = {row["kind"] for row in results}
    if "acknowledgements" in kinds:
        score_acknowledgements(results, replay_set["acknowledgements"])
    if "checks" in kinds:
        score_checks(results, replay_set["checks"])
    if "classifications" in kinds:
        score_classifications(results, replay_set["classifications"])
    fidelity(results, replay_set)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--kinds", nargs="*", default=list(_KINDS), choices=_KINDS)
    parser.add_argument("--judges", nargs="*", default=None)
    parser.add_argument("--only", nargs="*", default=None, help="moment ids to replay")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--score", type=Path, nargs="*", help="score earlier results instead")
    args = parser.parse_args()
    replay_set = json.loads(REPLAY_SET.read_text(encoding="utf-8"))
    if args.only:
        replay_set = {
            kind: [item for item in items if item["id"] in args.only] for kind, items in replay_set.items()
        }
    if args.score:
        results = [row for path in args.score for row in json.loads(path.read_text(encoding="utf-8"))]
    else:
        results = asyncio.run(
            run(replay_set, kinds=args.kinds, judge_names=args.judges, repeats=args.repeats)
        )
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    score(results, replay_set)


if __name__ == "__main__":
    main()

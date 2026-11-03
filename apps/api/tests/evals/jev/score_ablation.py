"""Score the live ablation's runs (`test_jev_ablation_live`), one row per run.

    uv run python -m tests.evals.jev.score_ablation ../../tmp/jev-eval/ablation/*.json

Times are seconds on the workstation's clock. A talk-over is an agent word played
while the scripted participant's microphone was sending a line; one within four
seconds of activity appended for the agent to speak to is counted apart. What the
agent said after a moment is its speech in a window that starts at the moment and
ends before the participant next speaks.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from app.connectors.openai_models import TokenUsage, cost_usd
from tests.evals.jev.judges import SOL

_BACKCHANNEL: Final = re.compile(r"^(?:(?:mm-?hmm|mhm|uh-huh|mm)[.!,]?\s*)*$", re.IGNORECASE)
_CHECKS_LINE: Final = "showed these agreed checks working"
_COMPLETE: Final = "Validation is complete"
#: What a question after "it all came back" names: each agreed check, and a redo.
_NAMES: Final[Mapping[str, re.Pattern[str]]] = {
    # "the saved view" names the view, not the save check.
    "save": re.compile(r"\b(save|saving|chosen name)\b", re.IGNORECASE),
    "restore": re.compile(
        r"\b(restor(?:e|es|ed|ing)\b(?! date)|came back|every filter|all (?:your|the|of the) filters)",
        re.IGNORECASE,
    ),
    "relative": re.compile(r"\b(rolling|relative|fixed dates|30 days|thirty days)\b", re.IGNORECASE),
    "redo": re.compile(r"\b(try (?:it|that) again|again|could you (?:refresh|reopen|save)|can you (?:refresh|reopen|save))\b", re.IGNORECASE),
}
#: Jev's listed gateway price, USD per million input tokens (2026-11-02); no output price.
JEV_INPUT_USD_PER_MILLION: Final = 0.042


def _agent_words(run: Mapping[str, Any], start: float, end: float) -> str:
    return "".join(
        text for at, speaker, text in run["words"] if speaker == "agent" and start <= at < end
    ).strip()


def _first_agent_word(run: Mapping[str, Any], start: float, end: float) -> float | None:
    return next(
        (at for at, speaker, _ in run["words"] if speaker == "agent" and start <= at < end), None
    )


def _next_participant_line(run: Mapping[str, Any], after: float) -> float:
    return min((started for _, started, _ in run["participant_lines"] if started > after), default=float("inf"))


def moment(run: Mapping[str, Any], name: str, *, until: str | None = None, window: float = 4.0) -> dict[str, Any]:
    """What the agent said right after a moment, before the participant spoke again."""
    at = run["events"][name]
    end = min(at + window, _next_participant_line(run, at))
    if until is not None:
        end = min(end, run["events"][until])
    said = _agent_words(run, at, end)
    first = _first_agent_word(run, at, end)
    return {
        "said": said,
        "spoke": bool(said),
        "beyond_backchannel": bool(said) and not _BACKCHANNEL.match(said),
        "first_word_after": None if first is None else round(first - at, 2),
    }


def validation_utterances(run: Mapping[str, Any], *, gap: float = 1.0) -> list[str]:
    """Each thing the agent said while they tried the new version, after which moment."""
    events = run["events"]
    words = [
        (at, text)
        for at, speaker, text in run["words"]
        if speaker == "agent" and events["debriefed"] <= at < events["said_done"]
    ]
    utterances: list[list[tuple[float, str]]] = []
    for at, text in words:
        if utterances and at - utterances[-1][-1][0] < gap:
            utterances[-1].append((at, text))
        else:
            utterances.append([(at, text)])
    described = []
    for utterance in utterances:
        at = utterance[0][0]
        after = max(
            (name for name in ("debriefed", "saved", "reloaded", "reopened") if events[name] <= at),
            key=lambda name: events[name],
        )
        said = "".join(text for _, text in utterance).strip()
        described.append(f"{said!r} {at - events[after]:.2f} s after {after}")
    return described


def _speakable_activity(run: Mapping[str, Any]) -> list[float]:
    """When the application appended activity for the agent to speak to."""
    return [at for at, kind, content in run["appends"] if kind == "say" and content.startswith("In the preview")]


def talk_overs(run: Mapping[str, Any], *, cue_seconds: float = 4.0) -> dict[str, Any]:
    """Agent words played while the participant spoke; those within `cue_seconds` of a
    speakable activity append are counted as caused by it."""
    cues = _speakable_activity(run)
    overlapping: list[tuple[float, str, str]] = []
    for line, started, ended in run["participant_lines"]:
        overlapping.extend(
            (at, text, line)
            for at, speaker, text in run["words"]
            if speaker == "agent" and started + 0.3 <= at <= ended
        )
    caused = [(at, text, line) for at, text, line in overlapping if any(0 <= at - cue <= cue_seconds for cue in cues)]
    return {
        "words": len(overlapping),
        "words_after_activity_cue": len(caused),
        "after_activity_cue": [f"{text.strip()!r} over {line[:30]!r}" for _, text, line in caused],
    }


def sol_cost(run: Mapping[str, Any]) -> tuple[int, float]:
    total = 0.0
    for usage in run["llm_usage"]:
        total += cost_usd(
            SOL,
            TokenUsage(
                input_tokens=usage["prompt_tokens"],
                cached_input_tokens=usage.get("cache_read_input_tokens") or 0,
                cache_write_input_tokens=usage.get("cache_creation_input_tokens") or 0,
                output_tokens=usage["completion_tokens"],
            ),
        )
    return len(run["llm_usage"]), total


def jev_cost(run: Mapping[str, Any]) -> tuple[int, float, list[float]]:
    calls = run["jev_calls"]
    tokens = sum(call["input_tokens"] for call in calls)
    return len(calls), tokens * JEV_INPUT_USD_PER_MILLION / 1_000_000, [call["seconds"] for call in calls]


def score(run: Mapping[str, Any]) -> dict[str, Any]:
    events = run["events"]
    walkthrough = _agent_words(run, events["walkthrough_started"], events["asked"])
    checks_lines = [content for _, _, content in run["appends"] if _CHECKS_LINE in content]
    speakable = [
        content for at, kind, content in run["appends"] if kind == "say" and "In the preview" in content
    ]
    over = talk_overs(run)
    llm_runs, llm_usd = sol_cost(run)
    jev_calls, jev_usd, jev_seconds = jev_cost(run)
    # Validation ends when the application cues the close, which it does in the same
    # transaction that completes the attempt; the harness only polls for it.
    settled = next(
        (
            at
            for at, kind, content in run["appends"]
            if kind == "say" and content.startswith(_COMPLETE) and at >= events["said_done"]
        ),
        None,
    )
    return {
        "arm": run["arm"],
        "started_at": run["started_at"],
        "criteria": [row["statement"] for row in run["criteria"]],
        "outcome": [[a["state"], a["outcome"]] for a in run["validation_attempts"]],
        "dashboard": moment(run, "dashboard_loaded", until="walkthrough_started"),
        "walkthrough_said": walkthrough,
        "saved": moment(run, "saved", until="reloaded"),
        "reloaded": moment(run, "reloaded", until="reopened"),
        "reopened": moment(run, "reopened", until="said_done"),
        "said_during_validation": validation_utterances(run),
        "checks_lines": checks_lines,
        "speakable_moments": len(speakable),
        "moments_judged": len(run["acknowledgement_moments"]),
        "questions_after_done": run["questions_after_done"],
        "said_done_to_recorded": None if settled is None else round(settled - events["said_done"], 1),
        "loaded_to_recorded": None if settled is None else round(settled - events["candidate_loaded"], 1),
        "talk_overs": over,
        "llm_runs": llm_runs,
        "llm_usd": round(llm_usd, 3),
        "jev_calls": jev_calls,
        "jev_usd": round(jev_usd, 5),
        "jev_p50": round(statistics.median(jev_seconds), 2) if jev_seconds else None,
    }


def named(question: str) -> list[str]:
    """Which agreed checks a question names, and whether it asks for a redo."""
    return [name for name, pattern in _NAMES.items() if pattern.search(question)]


def _median(values: Sequence[float | None]) -> str:
    present = [value for value in values if value is not None]
    return f"{statistics.median(present):.2f}" if present else "—"


def summary(rows: Sequence[Mapping[str, Any]]) -> None:
    """One line per arm: medians and counts over its runs."""
    print(
        "| Arm | Runs | Validated | Spoke after the dashboard | Spoke after the save | "
        "Spoke after the reload | Spoke after the reopen | First word after a moment, median | Questions after "
        "\"it all came back\" | Checks those questions named | Redo requests | "
        "\"It all came back\" to recorded, median | Talk-over words after an activity cue | "
        "Sol runs, median | Sol USD per interview, median | Jev calls, median |"
    )
    print("| --- " * 16 + "|")
    for arm in ("jev", "sol", "quiet"):
        runs = [row for row in rows if row["arm"] == arm]
        if not runs:
            continue
        moments = ("dashboard", "saved", "reloaded", "reopened")
        spoke = {m: sum(run[m]["spoke"] for run in runs) for m in moments}
        firsts = [run[m]["first_word_after"] for run in runs for m in moments]
        questions = [q for run in runs for q in run["questions_after_done"]]
        names = [named(q) for q in questions]
        print(
            f"| {arm} | {len(runs)} | {sum(run['outcome'] == [['completed', 'validated']] for run in runs)} "
            + " ".join(f"| {spoke[m]} of {len(runs)}" for m in moments)
            + f" | {_median(firsts)} s | {len(questions)} "
            f"| {sum(len([n for n in ns if n != 'redo']) for ns in names)} "
            f"| {sum('redo' in ns for ns in names)} "
            f"| {_median([run['said_done_to_recorded'] for run in runs])} s "
            f"| {sum(run['talk_overs']['words_after_activity_cue'] for run in runs)} "
            f"| {_median([run['llm_runs'] for run in runs])} "
            f"| {_median([run['llm_usd'] for run in runs])} "
            f"| {_median([run['jev_calls'] for run in runs])} |"
        )


def main(paths: Sequence[str]) -> None:
    rows = [score(json.loads(Path(path).read_text(encoding="utf-8"))) for path in paths]
    for row in sorted(rows, key=lambda r: (r["arm"], r["started_at"])):
        print(json.dumps(row, indent=1, ensure_ascii=False))
    summary(rows)


if __name__ == "__main__":
    main(sys.argv[1:])

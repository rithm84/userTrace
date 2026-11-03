"""Replay the recorded run's last validation turn, with and without Jev's checks lines.

    uv run python -m tests.evals.jev.counterfactual --export session-2026-11-01.json \
        --samples 10 --out counterfactual.json

The agent's context is rebuilt from the recorded session: the voice agent's
instructions, every turn as spoken, the participant's activity as the activity feed
described it (grouped where the person paused or someone spoke), the cues the
application sent at the agreements, the builds, and the new versions, and its tools.
It ends where the participant said the new version works ("It looks like it works
perfect, and the positioning is good too"), and Sol writes the agent's next reply
`--samples` times in each arm:

- `jev`: the two lines Jev's checks judgement sent (the save shown, then the
  refresh-and-reopen shown), with the activity that showed each.
- `sol`: no such lines, and the instructions and debrief cue ask the agent to judge
  from the activity which checks were shown, as production now does. The `jev` arm
  restores the wording that said the application reports them (`measured`).

The context is approximate: tool calls the agent made are left out (the words the
application spoke for them stay), and the cues' wording comes from today's code.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from openai import AsyncOpenAI

from app.interview.voice import cues
from app.interview.voice.activity_feed import _PREFIX
from app.interview.voice.tools import TOOL_DEFINITIONS
from tests.conftest import _load_env_file
from tests.evals.jev import measured
from tests.evals.jev.judges import SOL, agent_instructions
from tests.evals.jev.recorded import _activity_rows, _describe

_FINAL: Final = "It looks like it works perfect"
#: A reply that asks again about the save or the restore, which the activity had shown.
_RELISTS: Final = re.compile(r"\b(save|saving|chosen name|restore|every filter|all your filters|filter set|everything)\b", re.IGNORECASE)
_RELATIVE: Final = re.compile(r"\b(relative|rolling|fixed dates)\b", re.IGNORECASE)
#: A pause this long in the person's activity starts a new note, as the feed would.
_GROUP_SECONDS: Final[float] = 1.5
_SAVE: Final = "01a1962a-a926-7a23-8d82-293ed6d57488"
_REOPEN: Final = "01a1962a-a928-7517-a8a3-a64100b9042b"


def _time(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


def _turns(export: Mapping[str, Any]) -> list[tuple[float, str, str]]:
    """Each turn with when it started: agent words joined, participant turns as committed."""
    fragments = export["fragments"]
    base = sorted(_time(f["recorded_at"]) - f["start_ms"] / 1000 for f in fragments if f["speaker"] == "agent")
    zero = base[len(base) // 2]
    turns: list[tuple[float, str, str]] = []
    last_end = 0.0
    for fragment in sorted(fragments, key=lambda f: f["start_ms"]):
        at = zero + fragment["start_ms"] / 1000
        speaker = fragment["speaker"]
        if turns and turns[-1][1] == speaker == "agent" and at - last_end < 2.5:
            turns[-1] = (turns[-1][0], speaker, turns[-1][2] + fragment["fragment_text"])
        else:
            turns.append((at, speaker, fragment["fragment_text"]))
        last_end = zero + fragment["end_ms"] / 1000
    return [(at, speaker, text.strip()) for at, speaker, text in turns]


def _decided_shown(export: Mapping[str, Any], check: str) -> float:
    """When Jev's judgement first accepted a check as shown."""
    question = f"check_shown::{check}"
    return next(
        _time(judgement["created_at"])
        for judgement in export["judgements"]
        if judgement["accepted"]
        and judgement["answers"].get(question, {}).get("choice") == "shown"
        and judgement["answers"][question]["confidence"] >= measured.CHECK_SHOWN_CONFIDENCE
    )


def _criteria(export: Mapping[str, Any], revision: int) -> list[Mapping[str, Any]]:
    return [row for row in export["criteria"] if row["criteria_revision"] == revision]


def _cues(export: Mapping[str, Any], turns: Sequence[tuple[float, str, str]], *, arm: str) -> list[tuple[float, str, bool]]:
    """The application's cues, placed just before the words they produced."""
    debrief = cues._DEBRIEF_CUE
    if arm == "jev":
        debrief = measured.reported_by_the_application(debrief)
    placed: list[tuple[float, str, bool]] = []
    for agreement in export["agreements"]:
        revision = agreement["acceptance_criteria_revision"]
        recorded = _time(agreement["recorded_at"])
        placed.append((recorded + 5.0, cues._BUILDING_CUE, False))
        loading = next(at for at, speaker, text in turns if speaker == "agent" and at > recorded and "loading in your preview" in text)
        placed.append((loading - 0.5, cues._READY_CUE, True))
        walk = next(at for at, speaker, _ in turns if speaker == "agent" and at > loading + 1.0)
        checks = " ".join(f"{n}. {row['statement']}" for n, row in enumerate(_criteria(export, revision), start=1))
        placed.append(
            (
                walk - 0.5,
                debrief.format(
                    changes="What this version shows differently: new: saved view name field "
                    "(top left of the screen); new: saved view save control showing \"Save view\" "
                    "(top center of the screen); new: saved view reopen control (top right of the screen).",
                    outcome=agreement["agreed_scope"]["intended_outcome"].rstrip(".") + ".",
                    checks=checks,
                ),
                True,
            )
        )
    return placed


def context(export: Mapping[str, Any], *, arm: str) -> tuple[str, list[dict[str, Any]]]:
    """The agent's instructions and its context up to the participant saying it works."""
    instructions = agent_instructions()
    if arm == "jev":
        instructions = measured.reported_by_the_application(instructions)
    turns = _turns(export)
    final_at = next(at for at, speaker, text in turns if speaker == "participant" and _FINAL in text)
    events: list[tuple[float, str, str]] = [
        (at, "assistant" if speaker == "agent" else "user", text) for at, speaker, text in turns if at <= final_at
    ]
    for at, cue, _speak in _cues(export, turns, arm=arm):
        events.append((at, "user", f"<application>{cue}</application>"))
    # The participant's activity, one note per burst, with Jev's checks lines in its arm.
    rows = [(at.timestamp(), item) for at, item in _activity_rows(export) if at.timestamp() < final_at]
    checks_at = {check: _decided_shown(export, check) for check in (_SAVE, _REOPEN)}
    statements = {str(row["id"]): row["statement"] for row in _criteria(export, 2)}
    remaining_after = {
        _SAVE: [statements[c] for c in statements if c != _SAVE],
        _REOPEN: [statements[c] for c in statements if c not in (_SAVE, _REOPEN)],
    }
    turn_times = [at for at, _, _ in turns]
    groups: list[list[tuple[float, Any]]] = []
    for at, item in rows:
        if groups and at - groups[-1][-1][0] < _GROUP_SECONDS and not any(groups[-1][-1][0] < t <= at for t in turn_times):
            groups[-1].append((at, item))
        else:
            groups.append([(at, item)])
    # Each checks line rides with the newest activity Jev had judged when it decided.
    ends = [group[-1][0] for group in groups]
    carried = {
        check: max(end for end in ends if end <= decided_at) for check, decided_at in checks_at.items()
    }
    reported: Mapping[str, Mapping[str, str]] = {}
    for group in groups:
        lines, reported = _describe([item for _, item in group], reported=reported)
        if not lines:
            continue
        end = group[-1][0]
        tail = ""
        if arm == "jev":
            for check, carried_at in carried.items():
                if end == carried_at:
                    tail += " " + measured.checks_report(shown=[statements[check]], contradicted=[], remaining=remaining_after[check])
        events.append((end + 0.01, "user", f"<application>{_PREFIX}{'; '.join(lines)}.{tail}</application>"))
    events.sort(key=lambda event: event[0])
    messages = [{"role": role, "content": content} for _, role, content in events]
    return instructions, messages


def _tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": {"type": "object", "properties": tool.properties, "required": tool.required},
        }
        for tool in TOOL_DEFINITIONS
    ]


async def sample(client: AsyncOpenAI, instructions: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    response = await client.responses.create(
        model=SOL.model_id,
        instructions=instructions,
        input=messages,  # type: ignore[arg-type]
        tools=_tools(),  # type: ignore[arg-type]
        parallel_tool_calls=False,
        reasoning={"effort": SOL.effort.value},
        service_tier=SOL.service_tier.value,
        max_output_tokens=2000,
        store=False,
    )
    calls = [
        {"name": item.name, "arguments": item.arguments}
        for item in response.output
        if item.type == "function_call"
    ]
    return {"said": response.output_text.strip(), "calls": calls}


async def run(export: Mapping[str, Any], *, samples: int) -> dict[str, list[dict[str, Any]]]:
    _load_env_file()
    results: dict[str, list[dict[str, Any]]] = {}
    async with AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=2) as client:
        for arm in ("jev", "sol"):
            instructions, messages = context(export, arm=arm)
            results[arm] = list(await asyncio.gather(*(sample(client, instructions, messages) for _ in range(samples))))
    return results


def tally(results: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, dict[str, int]]:
    """Per arm: replies, those asking about a shown check again, about the relative range,
    and those that recorded instead of asking."""
    return {
        arm: {
            "replies": len(replies),
            "asks_about_shown_checks": sum(bool(_RELISTS.search(reply["said"])) for reply in replies),
            "asks_about_relative_range": sum(bool(_RELATIVE.search(reply["said"])) for reply in replies),
            "records": sum(any(call["name"].startswith("record") for call in reply["calls"]) for reply in replies),
        }
        for arm, replies in results.items()
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--export", type=Path)
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--show-context", choices=("jev", "sol"))
    parser.add_argument("--tally", type=Path, nargs="*", help="tally earlier results instead")
    args = parser.parse_args()
    if args.tally:
        merged: dict[str, list[dict[str, Any]]] = {}
        for path in args.tally:
            for arm, replies in json.loads(path.read_text(encoding="utf-8")).items():
                merged.setdefault(arm, []).extend(replies)
        print(json.dumps(tally(merged), indent=1))
        return
    export = json.loads(args.export.read_text(encoding="utf-8"))
    if args.show_context:
        _, messages = context(export, arm=args.show_context)
        for message in messages:
            print(f"{message['role']:9} {message['content'][:220]}")
        return
    results = asyncio.run(run(export, samples=args.samples))
    if args.out:
        args.out.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(tally(results), indent=1))
    for arm, replies in results.items():
        print(f"\n## {arm}")
        for reply in replies:
            print(f"- {reply['said']!r} calls={[call['name'] for call in reply['calls']]}")


if __name__ == "__main__":
    main()

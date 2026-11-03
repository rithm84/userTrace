"""Does Jev earn its place in the live interview?

Jev answered three questions during an interview: whether what the participant just
did is worth a few spoken words (`judge_acknowledgement`), which agreed checks their
activity in a new version already showed (`judge_checks_shown`), and, before the
gate, which flow the interview established (`propose_flow_classification`). The
voice agent's own model, GPT-6.1 Sol, already hears the same activity as
application notes. This evaluation asks whether Sol, or a plain rule, makes those
judgements as well. Following it, production stopped asking Jev about shown checks;
`measured` keeps that question as it was measured.

- `export_session` copies one recorded session out of a database, read-only.
- `recorded` rebuilds every moment Jev judged in such an export, with the state Jev
  was given and the answer it returned.
- `scenarios` writes further Saved Conversion Views moments, each labeled with why.
- `build_replay_set` writes the labeled set, `replay_set.json`, which holds a
  production session's transcript and activity and so is not committed.
- `judges` asks Jev, Sol, and the rules the same question; `replay` scores them.
- `counterfactual` replays the recorded run's last validation turn with and without
  Jev's checks lines.
- `test_jev_ablation_live` holds whole interviews over a real call with Jev deciding
  acknowledgements, and with Sol deciding them itself; `score_ablation` scores its
  runs.
"""

# O6 real-model task record

Recorded on 2026-10-02 (Asia/Shanghai). These are three maintenance tasks on
historical snapshots of this Python repository. They are a small, related
sample, not an estimate of general coding success. The existing 20 deterministic
cases remain framework regression checks and are not included in these results.

## Reproduce

Run the current pyharness TUI from its checkout. Create each target workspace
from a fresh, empty directory using `git archive <base-commit> | tar -xf - -C
<workspace>`, then run `uv sync --locked` in that workspace. Do not expose the
later fix commits to the agent. Set `DEEPSEEK_API_KEY` in the shell and start:

```sh
uv run --locked python -m coding_agent.tui \
  --workspace <workspace> --provider-id openai --model deepseek-chat \
  --base-url https://api.deepseek.com/v1 \
  --api-key-env DEEPSEEK_API_KEY --max-steps 30
```

The model was `deepseek-chat` through the OpenAI-compatible endpoint above.
Temperature and context-window overrides were unset. Each task used its own
`.runtime/session.jsonl`; all proposed changes and commands required TUI
approval. The workspaces have no non-target changes to their tracked snapshot
files. `.venv` and `.runtime` are generated locally. API keys were not written
to the sessions or this report.

The initial task prompts were:

1. Task 1: Fix report output so identical JSON and Markdown paths produce a
   clear error before writing, preserve existing files, add a regression test,
   and run `test/test_eval_api.py`. Feedback turns covered case-insensitive
   paths and a pre-existing probe filename.
2. Task 2: Allow `run_evaluation()` to receive the Provider and model settings
   used by the actual run, preserve defaults, add a configuration propagation
   test, and run `test/test_eval_api.py`. One feedback turn addressed custom
   Evaluator compatibility and a genuinely non-default Provider id.
3. Task 3: A correct artifact with a failed Agent run must fail the task;
   tool coverage must be reported separately and must not determine task
   success. Add a failed-run counterexample and run the related eval tests.

## Results

| Task | Base commit | Requested outcome | Observed result |
| --- | --- | --- | --- |
| 1. Report path collision | `088e0e5` | Reject JSON/Markdown paths that refer to one file before either report is written. | Code and regression tests pass after several feedback turns. Two early runs exceeded the step limit. TUI pytest failed before the sandbox fixes; terminal verification passed. This is artifact success, not a complete in-TUI verification. |
| 2. Provider configuration | `088e0e5` | Pass a non-default Provider/model configuration through the evaluation API without breaking defaults. | Initial code broke an existing custom Evaluator test. After one feedback turn it passed, and the resumed real-model TUI run proposed a pytest command that returned `exit_code: 0`, `6 passed`. This is the complete TUI path. |
| 3. Task success and tool coverage | `a3caec9` | Require run and artifact success while reporting tool coverage separately. | The agent added the failed-run guard and two tests, but did not separate tool coverage. Repeated pytest attempts hit sandbox errors; the run was interrupted, and Session recovery paired the pending command with an unknown-outcome error. Task incomplete. |

Task 1 changed `evals/reports.py` and `test/test_eval_api.py`. Its focused
tests were 16 passed; the snapshot's restricted-environment suite was 161
passed, 1 deselected. Task 2 changed `evals/api.py`, `evals/evaluator.py`,
`evals/runner.py`, and `test/test_eval_api.py`. Its affected tests were 17
passed; the snapshot suite was 158 passed, 1 deselected. Task 3 changed
`evals/scorers.py`, `test/test_eval_scorers.py`, and `test/test_evaluator.py`.
Its affected tests were 60 passed and its snapshot suite was 160 passed,
1 deselected. Passing Task 3's current tests does not satisfy its missing
tool-coverage requirement.

Task 2 took 358.61 seconds across its three TUI runs. The Provider responses
reported 431,810 input and 7,208 output tokens across those runs. Exact
per-run elapsed time and token usage were not collected for the user-operated
Task 1 or interrupted Task 3. Monetary cost was not recorded; no unverified
price was applied to the token counts. The API model revision was not pinned.

The terminal checks used the target workspace's locked virtual environment:

```sh
env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest \
  -p no:capture -p pytest_asyncio.plugin -q <relevant-test-files>
```

This host's ordinary pytest capture has previously exited with code 139.
The one deselected test requires the disabled capture fixture. Environment
failures and the task outcomes above are reported separately.

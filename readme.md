# pyharness

A small Python agent harness with workspace tools and deterministic evaluation cases.

## Development checks

```sh
uv sync --locked
uv run --locked pytest -q
```

The existing deterministic catalog contains 20 cases; it checks the evaluation framework and does not measure real-world coding success.

## Evaluation results

`evals.api.run_evaluation()` runs each case in a temporary workspace and produces JSON and Markdown reports. Each case reports three separate outcomes:

- `run_success`: the agent run finished without a failure.
- `artifact_success`: every expected file has the expected content.
- `task_success` (also exposed as the existing `passed` field): the run and artifact checks passed, including the observed tool-error condition for recovery cases. The summary `success_rate` counts these task successes.

`EvalCase.expected_tool_names` is a diagnostic trajectory expectation. The runner records tool names when `ToolStarted` fires, including attempts that later return errors. Reports show the observed and missing names, plus `tool_coverage_passed` and a separate coverage rate. Tool coverage does not change task success: another tool route may produce the correct artifact.

## Sessions and context budget

Pass `--session history.jsonl` to continue a local session. New sessions use JSONL format v2 with run and turn IDs plus a terminal run status. Existing v1 sessions are migrated when resumed. The saved workspace, model configuration, and system prompt must match; otherwise the CLI returns `session_error` and leaves the session unchanged.

On resume, an unfinished run is marked `interrupted`. Any tool call without a durable result receives an error result saying its execution outcome is unknown. The agent does not automatically repeat that call. A torn final JSONL record is removed; a complete final record missing only its newline is repaired. Other malformed records remain errors.

With `--context-window`, the agent checks the budget before **every** model request. The estimate includes the system prompt, messages, tool schemas, a conservative safety margin, and reserved output tokens. Earlier complete turns may be summarized in bounded chunks. Large tool results may be shortened in the request view while their full content stays in the session. If the request still cannot fit, the run returns `context_budget_exceeded`; summary errors return `compaction_failed`.

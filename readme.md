# pyharness

A small Python agent harness with workspace tools and deterministic evaluation cases.

## Quick start

Requires Python 3.11+, `uv`, and an OpenAI-compatible model API. Install the
launcher once from the pyharness checkout, then start it in the target project:

```sh
uv tool install --editable /path/to/pyharness
cd /path/to/python-project
lario
```

If `lario` is not on your PATH after installation, run `uv tool update-shell`
and restart the shell. The workspace defaults to the directory where `lario`
starts. Confirm the workspace trust prompt before the welcome screen appears;
declining exits without creating workspace files. The prompt appears on every
launch.

Configure the model in the TUI before sending a task:

```text
/model deepseek-chat
/base-url https://api.deepseek.com/v1
/api-key-env DEEPSEEK_API_KEY
/max-steps 30
/settings
```

Set `DEEPSEEK_API_KEY` in the shell before starting; only the variable name is
saved, never its value. `/provider`, `/context-window`, `/session`, and `/help`
are also available. Settings are saved in `.runtime/lario.json` inside the
workspace. Existing command-line options still work as launch-time overrides.
If a model or provider change conflicts with the current session, select a new
workspace-relative file with `/session .runtime/new-session.jsonl`.

Enter a task and press Enter; Shift+Enter inserts a newline. Review each
proposed file diff before applying it. The agent may also propose a project
test or build command: review its full arguments and working directory before
approving. The TUI shows the command's exit code and bounded output in the
session log, even when steps are hidden. A nonzero command exit does not itself
end the agent run.

Drag to select conversation text and press Ctrl+C (or Command+C) to copy it.
Use the Stop button to interrupt a running task.

## Read-only subagents

Start the CLI with `--multi-agent`, or enter `/multi-agent on` in the TUI.
The supervisor can call `delegate_task` for a short investigation. A subagent
has its own conversation and can only read, list, glob, and search workspace
files. It cannot edit files, run commands, or delegate again. Each subtask has
at most four model steps; parent and child requests share `--max-steps`.
The parent's saved conversation contains the delegated result. When a session
is enabled, complete subtask messages are saved separately in
`<session>.subtasks.jsonl` with mode `0600`. Sessions cannot switch between
single and multi-agent mode on resume. A subtask error is returned as a tool
error for the supervisor to handle.

Compare both modes on fresh temporary workspaces with the same deterministic
case catalog:

```sh
uv run --locked python -m evals.compare \
  --model deepseek-chat --base-url https://api.deepseek.com/v1 \
  --api-key-env DEEPSEEK_API_KEY --max-steps 10 \
  --output reports/multi-agent-comparison.json
```

The report includes paired task outcomes, total parent-and-child tokens,
median elapsed time, and delegation count. Add `--input-price`,
`--cached-input-price`, and `--output-price` together to estimate USD cost per
million tokens at a verified price. The catalog verifies small file operations;
it does not measure real repository coding success. Compare real coding tasks
separately using fixed Git snapshots and hidden acceptance checks. Use
`--case-set research` for three cross-file investigation cases; inspect
`subtasks` to see whether the model actually delegated.

The default session is `.runtime/session.jsonl` inside the workspace. Relaunch
`lario` to resume it; keep the workspace, model settings, and system prompt
unchanged. Sessions can contain source code and tool output, so keep them local
and private. `/session another/path.jsonl` switches to or creates a separate
workspace-relative session. Saved turns become model context on the next task;
the TUI does not repaint old turns when opening a session.

## Optional TencentDB memory

With MemoryCore running, set these environment variables before launching
`lario` (use the Team, Agent, and business User IDs from its panel):

```sh
export LARIO_MEMORY_URL=http://127.0.0.1:8420
export LARIO_MEMORY_SERVICE_ID=default
export LARIO_MEMORY_TEAM_ID=YOUR_TEAM_ID
export LARIO_MEMORY_AGENT_ID=YOUR_AGENT_ID
export LARIO_MEMORY_USER_ID=YOUR_USER_ID
export LARIO_MEMORY_API_KEY=YOUR_GATEWAY_KEY
```

Set the gateway key in the environment only. The local deployment's gateway key
is configured by `MEMORY_CORE_GATEWAY_API_KEY` (default `local`); it is distinct
from the panel's user key. Memory is off when `LARIO_MEMORY_URL` is unset. When
enabled, each task searches up to three L1 memories (at most 1200 characters
in the request), then sends only the completed user turn and final answer to
MemoryCore L0. JSONL remains the complete local history. Memory service errors
do not stop the task. Set `/context-window` to account for memory in the request
budget. Use a separate Team/Agent identity for each workspace whose memories
must remain isolated. Turns longer than 8192 characters are kept in JSONL but
not sent to MemoryCore.

## Streaming answers

The TUI displays OpenAI-compatible chat completion text as it arrives. Tool calls
are assembled before execution, and only complete model responses are saved in
the session. Stopping a stream marks the run interrupted and shows any partial
answer as unsaved text. Providers without streaming support still use the
existing complete-response path.

Model requests retry once after 0.25 seconds on HTTP 429, 5xx, or connection
errors. Requests do not retry after any streamed text has been shown.

## Verification commands

The agent can propose one existing project check with `run_command` (an argument
list and workspace-relative working directory). The TUI shows the complete
command and resolved directory for approval. An approved command runs through
macOS `sandbox-exec` with workspace-scoped writes, no network access, a 30-second
limit, and a 32 KB output limit. Rejected commands and systems without
`sandbox-exec` do not execute. Stopping the run terminates the command process
group. The tool does not automatically rerun commands with unknown outcomes.
This feature requires macOS; `sandbox-exec` is deprecated by Apple.
The sandbox allows reads in the workspace, system directories, and the approved
executable's runtime; writes are limited to the workspace. `/dev/null` is
available for tools such as pytest. There is no CPU, memory, or disk quota, and
an approved command can still change workspace files. Command execution fails
closed when the sandbox is unavailable.

## Development checks

```sh
uv sync --locked
uv run --locked pytest -q
```

The existing deterministic catalog contains 20 cases; it checks the evaluation framework and does not measure real-world coding success.
See [O6_REAL_TASKS.md](O6_REAL_TASKS.md) for reproducible real-model task
records, including failures and verification limits.

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

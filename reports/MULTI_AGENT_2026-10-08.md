# Supervisor + Subagent experiment

Run on 2026-10-08 (Asia/Shanghai) with `deepseek-chat` through the
OpenAI-compatible endpoint `https://api.deepseek.com/v1`. Each case started in
a fresh temporary workspace. Single and multi-agent modes received the same
task text, model, tools other than `delegate_task`, and total limit of 10 model
requests. Case order alternated which mode ran first. Temperature used the
Provider default of 0.0. The model revision was not pinned. MemoryCore was off.
Costs are omitted because no verified token price was supplied.

| Case set | Single success | Multi success | Single tokens | Multi tokens | Single median | Multi median | Delegations |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 20 existing deterministic cases | 17/20 | 16/20 | 73,387 | 76,085 | 2.56 s | 2.54 s | 0 |
| 3 prompted cross-file investigations | 3/3 | 3/3 | 22,066 | 39,191 | 5.24 s | 8.82 s | 3 |

In the 20-case catalog, one task passed only in single-agent mode; no task
passed only in multi-agent mode. The model never delegated, so this measures
the cost of offering another tool, not the benefit of collaboration. In the
three investigation cases, the prompt explicitly requested delegation when
available. Both modes completed all three; delegation raised token use by
77.6% and median elapsed time by 68.3% without improving success. These are
small file-operation tasks, not real repository fixes. Three cases cannot
establish a general effect, and the API model may change between runs.

Raw paired results: [catalog](multi-agent-catalog-final-2026-10-08.json) and
[delegation diagnostic](multi-agent-research-delegated-2026-10-08.json).

The current evidence does not show a benefit from enabling delegation by
default. A claim about coding-task improvement requires a frozen set of real
repository snapshots, hidden acceptance checks, repeated paired runs, and
verified pricing. The existing O6 three-task record is too small and lacks
complete matched single/multi runs for that claim.

The final security guard on the optional subtask history sidecar was added
after these model runs. It does not affect runs without sessions; the paired
evaluations above used no session file.

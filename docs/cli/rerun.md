# Run tasks again

Chosen tasks of an ended job run again in that job; the new result takes the task's place, the earlier tries stay.
A job its cap, a stop or a kill interrupted is finished the same way: its tasks with no result run in it.

```bash title="Run tasks again"
nullius standard rerun JOB_DIR (--unit ID ... | --units FILE | --unfinished) [--reason TEXT] [-j N] [--retries N] [--max-usd USD]
```

| Flag | Does |
| --- | --- |
| `--unit ID` | A trial's name, or a unit's id for each of its attempts; repeatable |
| `--units FILE` | The ids a file lists, one a line, as [a run's list](run.md#a-list-of-units) |
| `--unfinished` | Every task the job planned with no result, and every trial the run's spending cap or a Stop interrupted; none: says so, exit 0 |
| `--reason TEXT` | Why, kept in the job's record |
| `-j N` | Units at once; the job's by default |
| `--retries N` | Automatic retries of each try, as [a run's](run.md#retries); 0 by default |
| `--max-usd USD` | The re-run's own [spending cap](run.md#a-spending-cap) |
| `--from-run` | Started by Nullius: `OPENAI_API_KEY` and `OPENAI_BASE_URL` only, saved settings unread |

From a job's page: [Run tasks again](../results.md#run-tasks-again), on the machine the job ran on.

| Rule | Detail |
| --- | --- |
| Which tasks | Every task named, whatever its result: a pass is run again as a failure is |
| What a job planned | Its live feed, `events.jsonl`: each trial queued before the first began. A job with no feed (a merged one) runs only trials it holds; `--unfinished` is refused |
| A task never begun | Run as its first try, under the name the run gave it |
| A task with no result | Its run was killed: its folder kept as `retried/<trial>.try-N` with `no-result.json` in it, and the task run again |
| The settings | The job's `config.json`, none given again; the endpoint and the key from the environment, as for a run |
| A try | As an automatic retry's: the trial's folder kept as `retried/<trial>.try-N`, the unit run again under the trial's name, its place and its attempt kept. A stopped try puts the one before back |
| Refused, nothing moved | No task named; an id the job never planned; a job still running or being run again; a merge of shards that differed (`merge --force`); a benchmark or an agent that resolves to another version than the job ran |
| A merged job | Run again in place; its shards' files stay as they were |
| The trial's record | Its `result.json`: `standard.retries`, the tries before it, and `standard.rerun`: by hand, when, the reason, the result it replaced, or `begun` for a first try |
| The job's record | `result.json` recomputed from the trials as [`merge`](shard-and-merge.md) does; its start, end and `budget` the first run's: its cap is not reopened; `reruns`: each re-run's time, reason, trials, results replaced, tasks `begun`, trials with `no_result`, cap and spend |
| Score and cost | The new result is the task's on every page; the job's cost adds every try |
| The live board | Running again from the re-run's start to its end; a task run again shows its attempt and the result before it |

<div class="grid cards" markdown>

- [Run a benchmark](run.md)

    The first run.

- [Read jobs](read-jobs.md)

    The tries, on the trial page.

</div>

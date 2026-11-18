# Task sets

A named list of a benchmark's units, kept with its revisions. A run names the set; its job records which revision ran.

```bash title="Commands"
nullius standard set create <benchmark> NAME [--dataset D] (--units FILE [--bench-option NAME=VALUE]... \
    | --from-job JOB_DIR [--outcome O,...]... [--unit ID]...) [--description TEXT]
nullius standard set update <benchmark> NAME (--add ID... | --remove ID... | --units FILE) [--note TEXT]
nullius standard set list [<benchmark>] [--json]
nullius standard set show <benchmark> NAME[@REV] [--json]
nullius standard set import PATH
nullius standard run <benchmark> <other> --set NAME[@REV] ...
```

```bash title="Examples"
nullius standard set create humaneval first-ten --dataset humaneval --units ten.txt
nullius standard set create humaneval misses --from-job jobs_standard/JOB --outcome failed,infra
nullius standard set update humaneval misses --remove HumanEval/12 --note "solved upstream"
nullius standard run humaneval reference --model gpt-5-mini --set misses@1 --unsandboxed
```

| Part | Detail |
| --- | --- |
| The entry | `sets/<benchmark>/NAME.json` in the user catalogue (`nullius paths`, `NULLIUS_CATALOGUE`): a plain file to copy or keep in git |
| What it holds | An id made at creation, the name and description, the benchmark and version, the dataset, the benchmark options not at their default, the setup, and its revisions |
| A revision | Its number, date, note, unit ids and a content hash over the benchmark, the dataset, the options, the setup and the sorted ids |
| A name | Letters, digits, `.`, `_` and `-`, starting with a letter or a digit; no `/`, no `..`. One that exists is refused: `update` changes it |
| Identity | Two runs ran the same set when their jobs name the same id. Two sets of equal lists stay two sets |

| Command | Rule |
| --- | --- |
| `create --units FILE` | The file's ids, read as [`run --units`](run.md#a-list-of-units) reads them; `--dataset` is needed. `--bench-option` names an option the list was made under |
| `create --from-job JOB_DIR` | The units a standard job ran, by each trial's full unit id; the job's dataset and options. `--outcome` takes the trials of those outcomes ([outcome names](../field-guide.md#outcomes), or `passed`, `failed`, `infra`), `--unit` adds the units named; neither takes every unit. A unit is taken when any of its attempts is |
| `update` | A change of membership adds a revision; the earlier ones stay. The same membership adds none. Removing a unit the set does not hold, or every unit, is refused |
| `show NAME@REV` | That revision; the latest without `@REV` |
| `import PATH` | An entry's file, or a job that ran a set: merged into the entry of that name here. Refused: another id under that name, or a revision of the same number with another hash |
| Not checked | The ids, until a run: the driver lists the units then |

## A run of a set

| Rule | Detail |
| --- | --- |
| Its dataset and options | The set's. A `--dataset` of another, or a `--bench-option` giving one of the set's options another value, is refused before any unit; other options stay the run's |
| `--units` | Refused beside `--set` |
| The units | The set's ids, as a [list of units](run.md#a-list-of-units): an id the driver does not list refuses the run before any unit; then `--shard`, then `--limit` |
| The record | `config.json`: `standard.task_set`, the set's id, name, revision, hash, units, and what `set import` rebuilds the entry from |
| Shards | `merge` refuses shards of two sets, two revisions, or a set and none, unless `--force`; the merged job keeps the first shard's set ([Shard and merge](shard-and-merge.md)) |
| On a host | Not yet: a recipe has no field for a set |

Exit codes: 0 done, 2 a usage error or a refusal, 3 no such benchmark, set, revision, file or job ([Exit codes and output](exit-codes.md)).

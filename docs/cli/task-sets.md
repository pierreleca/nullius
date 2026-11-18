# Task sets

A group of a benchmark's units, kept with its revisions and run as the benchmark's own dataset is: a run names the set, gives its own options, and its job records which revision ran. A set holds no option and no setup.

```bash title="Commands"
nullius standard units <benchmark> --dataset D [--bench-option NAME=VALUE]... [--search TEXT] [--category FIELD=VALUE]... [--json]
nullius standard set create <benchmark> NAME [--dataset D] (--units FILE | --search TEXT | --category FIELD=VALUE... \
    | --from-job JOB_DIR [--outcome O,...]... [--unit ID]...) [--description TEXT]
nullius standard set update <benchmark> NAME (--add ID... | --remove ID... | --units FILE) [--note TEXT]
nullius standard set list [<benchmark>] [--json]
nullius standard set show <benchmark> NAME[@REV] [--json]
nullius standard set rename <benchmark> NAME NEW
nullius standard set delete <benchmark> NAME
nullius standard set import PATH
nullius standard run <benchmark> <other> --set NAME[@REV] ...
```

```bash title="Examples"
nullius standard units osworld1 --dataset test_nogdrive --search tab --category domain=chrome
nullius standard set create humaneval first-ten --dataset humaneval --units ten.txt
nullius standard set create osworld1 chrome-tabs --dataset test_nogdrive --search tab --category domain=chrome
nullius standard set create humaneval misses --from-job jobs_standard/JOB --outcome failed,infra
nullius standard set update humaneval misses --remove HumanEval/12 --note "solved upstream"
nullius standard set rename humaneval misses misses-june
nullius standard run humaneval reference --model gpt-5-mini --set misses@1 --unsandboxed
```

## Listing a dataset's tasks

| Part | Detail |
| --- | --- |
| What it gives | Each task's id, title, text and categories, the number matched of the dataset's, and each category's values with their counts |
| Title, text, categories | From the driver's [`describe`](../standard.md#driver-ops), read from the dataset's files. A driver without it gives ids alone; a search then reads the ids |
| `--search TEXT` | The tasks whose id, title or text hold each word, whatever the case |
| `--category FIELD=VALUE` | Values of one field widen the match, fields narrow it. A field no task has is refused. Each field's values are counted over the tasks the search and the other fields match |
| `--bench-option` | The options the driver lists under; the others at their defaults |
| What it needs | The benchmark's upstream, its dataset file and its driver's environment, prepared as a run prepares them when missing; with [`driver.list_command`](../standard.md#driver-ops), the upstream and the dataset file alone. No unit, VM or container is started; no licence or key is given |
| A first listing | One line on stderr names what it fetches. A program it needs that this machine lacks (`git`, `uv`, the light command's) is refused before anything is fetched |
| Before a listing | `nullius.standard.units.state(benchmark, dataset, options)` says, starting and fetching nothing: `cached`, `light` (what it fetches), `full` (the driver's environment, and what it fetches), or `refused` (the reason) |
| The cache | The user cache (`nullius paths`): the same benchmark, driver, dataset and options are listed once |

Exit codes: 0 listed (no match included), 2 a usage error or a refusal (an option or a category not known, a program not on this machine, a driver that could not list), 3 no such benchmark or dataset.

## The set

| Part | Detail |
| --- | --- |
| The entry | `sets/<benchmark>/NAME.json` in the user catalogue (`nullius paths`, `NULLIUS_CATALOGUE`): a plain file to copy or keep in git |
| What it holds | An id made at creation, the name and description, the benchmark, the dataset, and its revisions. An entry holding any other field is refused where it is read. One of the earlier format, which kept a run's options: delete it and make it again |
| A revision | Its number, date, note, unit ids and a content hash over the benchmark, the dataset and the sorted ids |
| A name | Letters, digits, `.`, `_` and `-`, starting with a letter or a digit; no `/`, no `..`. One that exists is refused: `update` changes it |
| Identity | Two runs ran the same set when their jobs name the same id. Two sets of equal lists stay two sets |

| Command | Rule |
| --- | --- |
| `create --units FILE` | The file's ids, read as [`run --units`](run.md#a-list-of-units) reads them; `--dataset` is needed |
| `create --search`, `--category` | The ids [the listing](#listing-a-datasets-tasks) matches that day, under the benchmark's default options; `--dataset` is needed. The revision's note keeps the filter. No match is refused |
| `create --from-job JOB_DIR` | The units a standard job ran, by each trial's full unit id; the job's dataset. `--outcome` takes the trials of those outcomes ([outcome names](../field-guide.md#outcomes), or `passed`, `failed`, `infra`), `--unit` adds the units named; neither takes every unit. A unit is taken when any of its attempts is |
| `update` | A change of membership adds a revision; the earlier ones stay. The same membership adds none. Removing a unit the set does not hold, or every unit, is refused |
| `show NAME@REV` | That revision; the latest without `@REV` |
| `rename NAME NEW` | The same set under another name, its id and revisions kept: a job that ran it finds it by its id and names it by its new name. `NEW` follows the rules of a name |
| `delete NAME` | The entry removed, an entry of the earlier format included. A job that ran it keeps its record |
| `import PATH` | An entry's file, or a job that ran a set (its description then empty, the revision dated by the import): merged into the entry of that name here. Refused: another id under that name, or a revision of the same number with another hash |
| Not checked | The ids, until a run: the driver lists the units then |

## A run of a set

| Rule | Detail |
| --- | --- |
| Its dataset | The set's. A `--dataset` of another is refused before any unit |
| Its options | The run's own, any `--bench-option` as on the whole dataset |
| `--units` | Refused beside `--set` |
| The units | The set's ids, as a [list of units](run.md#a-list-of-units): ids the driver does not list under the run's options refuse the run before any unit, the message giving how many and the first five; then `--shard`, then `--limit` |
| The record | `config.json`: `standard.task_set`, the set's id, name, revision, hash and units |
| Shards | `merge` refuses shards of two sets, two revisions, or a set and none, unless `--force`; the merged job keeps the first shard's set ([Shard and merge](shard-and-merge.md)) |
| On a host | From the app: the runner snapshot sent to the host carries the recipe's sets, and the run there reads them from its copy of the catalogue. By hand: copy the entry, or `set import` a job that ran it |

Exit codes: 0 done, 2 a usage error or a refusal, 3 no such benchmark, set, revision, file or job ([Exit codes and output](exit-codes.md)).

## On the pages

| Page | What it does |
| --- | --- |
| A benchmark's page, **Task sets** | Its sets (name, dataset, number of tasks, latest revision, date) and a link to each dataset's tasks |
| A dataset's tasks | A search over id, title and text, each category's values with their counts, 100 tasks a page, under the benchmark's default options. Tick a task, select every match, show the selected, paste ids. **Save as task set** keeps the ticked ids under a name and a description |
| A set's page | The same page with its members ticked: **Save as a new revision**, with a note, adds a revision only when the membership changed. An earlier revision is read only. **Rename** and **Delete** ask in place; the delete names the set, its number of tasks and its revisions |
| A first listing | Never made inside a request. In the cache: shown at once. One that [`driver.list_command`](../standard.md#driver-ops) makes lightly: started by the page, which says what it fetches. One that needs the driver's own environment: not started, the page says what it needs; ids can still be pasted |
| The builder | Each chosen dataset: all its tasks or one of its sets; Review names the set and its number of tasks |
| A job's page | The set, by its name now, and the revision it ran, linked when the catalogue here holds them; a set deleted since, by the name the job recorded, without a link. **Save as task set** keeps the units of the trials its outcome filter shows, by their full ids |

Saving, renaming and deleting need a browser on the application host. A name that exists, an empty name or one with a slash is refused on the page.

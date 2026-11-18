# The computer port: `agent.computer/v2`

The desktop of [version 1](agent-computer-v1.md), under one more rule: once the agent is given its task, it reaches the desktop through acting calls alone, and everything it learns of the desktop comes in an acting call's answer. One acting call is one step, as in version 1. Where this page is silent, version 1's holds.

An agent tells the two versions apart from `tools/list`: version 2 has no `python` and no `read`, has `prepare`, and its `step` lists `files`. An agent with a driver of its own uploads it and starts it with `prepare` before the task, and talks to it through a step's actions and files.

## The window before the task

| Rule | Detail |
| --- | --- |
| Opens | When the agent's session opens |
| Closes | When the run sends the agent its task: Nullius tells the benchmark first, and the task waits until the window is closed. A session that starts with its task already given, such as a command that takes the prompt, has no window |
| In it | `upload` and `prepare`, as often as needed, neither a step; `info` and `screenshot`. An acting call is refused and takes no step |
| After it | `upload` and `prepare` are refused, with words that say the task has been given |

## Reading

None of these is a step.

| Tool | Arguments | Result |
| --- | --- | --- |
| `info` | none | `{screen: [width, height], steps, steps_left, pause, password, folder}`. `folder`: a folder of the desktop that is the agent's own, made empty for the unit |
| `screenshot` | none | The observation of the last step, as PNG. Before the first step, the picture the benchmark took at the start, before the window. It takes no new picture: two calls with no step between them return the same bytes |
| `upload` | As version 1; `path` inside `folder` (absolute, or relative to it, with no `.` or `..` part) | `{size}`. In the window alone |
| `prepare` | `actions`: a list of texts, each Python run in the desktop as a step's action is, with no pause after it (`WAIT` does nothing); `files`: as for `step` | `{files: {PATH: BASE64 or null}}`, once the actions have run. In the window alone; an action the desktop does not end in time is an error and stops it |

## Acting

As version 1: `click`, `move`, `drag`, `scroll`, `type`, `key`, `wait`, `step`, `done`, `fail`. `wait` is the way to look again: one step, the pause, a fresh observation.

| `step` argument | In version 2 |
| --- | --- |
| `actions`, `response`, `screenshot`, `end` | As version 1. An empty list is a step with no action |
| `files` | Optional: any number of paths inside `folder`. Once the actions have run and their pauses have passed, each file is read: the answer is `{steps_left, done, files: {PATH: BASE64 or null}}`, null for a file that is not there. A file over 12 MiB, or one the desktop cannot read, is `{PATH: {error}}` for that file alone; the step has run all the same. A path outside `folder` refuses the call at once: it takes no step and never reaches the desktop |

An acting call's image, and `step`'s with `screenshot` true, is the observation of that step: the bytes `screenshot` returns afterwards. A whole answer is at most 24 MiB, as in version 1: the files are taken in their order while the answer holds them, with its image, and each file that would take it past 24 MiB is `{PATH: {error}}`. The same holds for `prepare`.

## The observation

| Rule | Detail |
| --- | --- |
| When | Fixed as a step that ran an action ends. A step that ran no action leaves it as it was |
| Which | The benchmark's own: for a loop that hands its agent an observation, such as [OSWorld1](../benchmarks/supported/osworld1.md)'s, the screenshot it hands for the next decision, with no capture added. For a loop that hands none, one picture taken once the step's last pause has passed |
| Between two steps | Nothing else of the desktop is shown. What `prepare` changed on the screen shows in the first step's observation |

## Rules that change

| Rule of version 1 | In version 2 |
| --- | --- |
| Reading: `python`, `upload`, `read`, `screenshot` are no step | `info` and `screenshot` read what the benchmark already holds; `upload` and `prepare` live in the window; `python` and `read` are gone |
| Input sent through `python` is outside the port | Gone with `python`. Input sent by `prepare` is outside the port |
| The record | As version 1, and the benchmark's records list every action's text: a step's actions pass through its loop; a `prepare` call's go to a record of their own, `{start, actions, files}` per call, written before they run |
| The limits | As version 1: `steps`, `steps_left`, `pause`. A `prepare` action is bounded as a step's action is |

## Known limits

- Inside the desktop, the agent's code can read and write wherever the desktop's user can and can create links outside `folder`; `folder` bounds the names of paths passed to `upload` and `files`, and the benchmark's records hold the code of every step action and `prepare` call.
- An action's code may start a process that goes on after its step, as an OSWorld action may. The agent learns what it did from a later step's answer alone.
- In the window the desktop already holds the task's files and windows. An agent that guessed its task from them could work there; the benchmark's records list every `prepare` call's code.
- An agent that is not sandboxed (the local backend) could reach the machine beside the port. A comparison that has to be trusted runs on the container backend.

## For a benchmark's runner

As version 1, with these changes:

| Piece | What the runner gives or does |
| --- | --- |
| `Bridge` | `version=2`; `folder`, an absolute path in the desktop; `window=True` when the benchmark's driver is told the task is given (else the session has no window); `prepared`, the file of the `prepare` record. The bridge makes `folder` empty at the loop's first `predict` |
| `Desktop` | The same five methods, and optionally `observation()`: the screenshot the benchmark's loop took as the step it just ran ended. Without it, the bridge takes one by `capture()` |
| The window's end | `driver.task_given` in the definition: Nullius sends `task_given` before the agent's task, and the driver closes the window (`Bridge.close_window`, or the lifeline's line `task-given`) before it answers |

## The kit

`cli conform AGENT --port agent.computer/v2` runs the kit `agent-computer-v2`: [version 1's eight checks](agent-computer-v1.md#the-kit), each on a desktop of its own reached through a version 2 server, and one more. The run tells the kit when it gives the agent its task; the window before it is open until then.

| Check | Kind | The task | Passes when |
| --- | --- | --- | --- |
| `late` | skill | A button appears a few seconds after the task is given; click it once | A left press on the button, every input inside an acting call, and an observation taken as a step ended, after the button appeared, reached the agent before the press: it looked again by a step |

Input sent by `prepare` fails a check as input sent by `python` does in version 1. Each check has the limits of version 1's; `late` has 8 steps. The desktop, its image and the reaper are version 1's kit's. The null control passes no check; the reference is given its task as it starts, makes version 1's calls and, for `late`, looks again by `wait` until a step's observation was taken with the button up, then clicks it: it passes all nine.

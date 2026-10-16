# Kernel Benchmarks

**How well do AI agents get everyday things done on the real web, and what makes them better?**

Booking a table, applying for a job, or finding a store's hours sounds easy until an agent has to do it on a live website. Success depends on more than the model:

- **Browser tools.** Is the agent driving Playwright code, a screenshot-and-click loop, or a purpose-built API?
- **Browser settings.** Is the browser stealthy? Which proxy does it use? Does the site even let it in?
- **Guidance.** Does the agent know what a site's bot protection looks for before it starts?

This repo measures those choices side by side. Each benchmark runs a set of real-website tasks under several configurations, called **arms**, in the same time window. You can then see what changed the outcome: the agent, its tools, or its browser.

Each attempt at one task under one arm is a **trial**. Trials run on [Harbor](https://github.com/harbor-framework/harbor), an open framework for agent evaluations. Every trial gets its own [Kernel](https://www.kernel.sh) cloud browser, created fresh and deleted afterwards.

### What you need

Running trials uses paid services:
- a Kernel account,
- an OpenAI or Anthropic key for the agent,
- for ClawBench, a [PurelyMail](https://purelymail.com) account with a mail domain you control and an LLM judge endpoint.

A full ClawBench run is 129 trials per arm. [Preparing tasks](#quick-start) for the static arms is free and needs no credentials, so you can inspect exactly what would run before spending anything.

## How a benchmark is put together

| Piece | What it is | Example |
|---|---|---|
| **Suite** | A set of tasks, adapted into Harbor tasks, with its own scoring. | [ClawBench](https://github.com/TIGER-AI-Lab/ClawBench): 129 tasks on real websites, scored by intercepting the final request |
| **Toolset** | What the agent uses to drive the browser. | `kernel-mcp-local`: the [Kernel MCP server](https://github.com/kernel/kernel-mcp-server), built from source |
| **Agent** | A Harbor agent, model, and version. | Codex with `gpt-5.6-luna` |
| **Arm** | One agent + toolset + browser configuration. The thing being compared. | `codex-kernel-mcp-site-config` |
| **Configurator** | Optional. Picks browser settings per task, and can add advice for the agent. | `site-config`: asks Kernel's [config registry](https://kernel.sh/docs/config-registry) what works on the task's site |
| **Benchmark** | A suite plus the arms to run on it. One GitHub workflow each. | `kernel-mcp-clawbench` |

A run turns those into trials:

```mermaid
flowchart LR
    suite[Suite generates tasks] --> arms
    subgraph arms [For each arm]
        configure[Configure browser<br/>static or configurator] --> toolset[Apply toolset]
    end
    arms --> harbor[Harbor runs trials<br/>in parallel]
    harbor --> report[Summary and<br/>Braintrust]
```

Inside a trial:

1. Harbor creates a headful Kernel browser with that task's settings. The browser is a full VM.
2. Inside the VM, Harbor builds the task's image with Docker and installs the agent in that container. Toolsets built from source, like the Kernel MCP server, are built here too, which is why each trial spends a few minutes on setup.
3. The agent completes the task by driving the VM's browser through its toolset.
4. The suite's verifier scores the result. Harbor then deletes the browser.

## Quick start

You need [uv](https://docs.astral.sh/uv/). Preparing tasks for arms without a configurator needs no credentials:

```bash
uv sync
uv run bench prepare kernel-mcp-clawbench \
  --arms codex-kernel-mcp --tasks v2-1134-chapter-finder-redcross --out /tmp/prepared
```

This writes ready-to-run Harbor tasks you can inspect: `task.toml`, the instruction, the Dockerfile, and `environment/kernel-config.json` with the browser settings.

To run trials, export the credentials listed below, then:

```bash
uv run bench run kernel-mcp-clawbench \
  --arms codex-kernel-mcp --tasks v2-1134-chapter-finder-redcross
```

Harbor writes one job directory per arm under `jobs/`, containing rewards, trajectories, and verifier output. To summarize jobs, run `bun report/report.ts --arm <name>=<job dir>` (needs [Bun](https://bun.sh)).

`--tasks` takes comma-separated task IDs or `all`. `--concurrency` sets concurrent trials per arm, and `--attempts` the trials per task and arm. `--toolset-ref` builds a toolset from a specific git ref, e.g. a Kernel MCP server branch. `--suite-arg KEY=VALUE` overrides one of the benchmark's `suite_args`, and `--toolset-arg KEY=VALUE` one of the `toolset_args` of the arms that set it.

The run also writes `jobs/run-<id>.json`, which maps each arm to its job directory, exit status, and the browser settings and site guidance each task got.

A run of two arms on two tasks (Red Cross and Indeed) produced this summary:

| Arm | Configuration | Lenient | Strict | Intercepted | Infra | Retries | Ungraded | Kernel MCP valid | Median calls | Median duration | Cost |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| codex-kernel-mcp | codex@0.120.0 · gpt-5.6-luna · config 14a94e93 | 1/2 | 1/2 | 1/2 | 0 | 0 | 0 | 2/2 | 9 | 216s | $0.0281 |
| codex-kernel-mcp-site-config | codex@0.120.0 · gpt-5.6-luna · config 14a94e93 | 1/2 | 1/2 | 1/2 | 0 | 0 | 0 | 2/2 | 16 | 302s | $0.0422 |

### Credentials

| Variable | Used by |
|---|---|
| `KERNEL_API_KEY` | Browsers, the config registry, and Kernel MCP. Use an [API key scoped](https://kernel.sh/docs/info/api-keys) to a [project](https://kernel.sh/docs/info/projects) used only for benchmarks; the run checks this before starting. |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | Codex and Claude Code arms; the `site-config` configurator's model |
| `PURELY_MAIL_API_KEY`, `PURELY_MAIL_DOMAIN` | ClawBench creates a disposable [PurelyMail](https://purelymail.com) mailbox on your domain for every task, so agents can sign up and receive confirmation emails. |
| `CLAWBENCH_JUDGE_BASE_URL`, `CLAWBENCH_JUDGE_API_KEY`, `CLAWBENCH_JUDGE_MODEL`, `CLAWBENCH_JUDGE_API_TYPE` | ClawBench's LLM judge, which checks the intercepted request against the task. Any OpenAI-compatible endpoint works with `CLAWBENCH_JUDGE_API_TYPE=openai-completions`; we use `deepseek/deepseek-v4-pro` through OpenRouter. See [ClawBench](https://github.com/TIGER-AI-Lab/ClawBench) for details. |
| `BRAINTRUST_API_KEY`, `BRAINTRUST_PROJECT` | Optional: publishing results |

## Running in GitHub Actions

Each benchmark has a manually triggered workflow:

```bash
gh workflow run kernel-mcp-clawbench.yml -f tasks=all -f concurrency=10
```

The job summary shows a table per arm. When Braintrust is configured, each run is also published there as one experiment with redacted trajectories. Raw job directories are never uploaded, because they contain task credentials.

GitHub stops jobs after six hours. Arms still running then are reported as incomplete. Lower the task count or raise `concurrency` for large runs.

## Current benchmarks

### `kernel-mcp-clawbench`

The Kernel MCP server on ClawBench.

| Arm | Agent | Browser |
|---|---|---|
| `codex-kernel-mcp` | Codex, `gpt-5.6-luna` | Stealth |
| `codex-kernel-mcp-site-config` | Codex, `gpt-5.6-luna` | From Kernel's config registry per site, plus the registry's guidance in the instruction. On Indeed, for example, it picks a US ISP proxy; the stealth-only arm was stopped by Cloudflare there. |
| `claude-kernel-mcp` | Claude Code, `claude-sonnet-5` | Stealth |

ClawBench's primary score is the lenient reward. `kernel_mcp_valid` records whether the agent called Kernel MCP's browser tool and used its assigned browser on every call, so a failed task can be told apart from a trial that never used the toolset. It reads the calls the Kernel MCP server received, logged by a stdio wrapper, so it works the same for agents that nest MCP calls, such as Codex's code mode.

### `xwebmcp-repl`

How much a site's [xwebmcp](https://github.com/kernel/xwebmcp) tools help an agent that drives the browser through Kernel MCP's browser REPL, on the `xwebmcp-tasks` suite: everyday tasks on the sites xwebmcp has tools for, such as finding the cheapest fare on delta.com or putting a coffee in the cart on bluebottlecoffee.com.

| Arm | Agent | Browser tools |
|---|---|---|
| `codex-repl` | Codex 0.163.0, `gpt-6-luna` | `browser_repl` |
| `codex-repl-xwebmcp` | Codex 0.163.0, `gpt-6-luna` | `browser_repl`, plus the site's xwebmcp tools registered in the browser before the agent starts |
| `claude-haiku-repl` | Claude Code 2.1.301, `claude-haiku-5-5` | `browser_repl` |
| `claude-haiku-repl-xwebmcp` | Claude Code 2.1.301, `claude-haiku-5-5` | `browser_repl`, plus the site's xwebmcp tools |

Every arm runs with the agent's own web search and fetch tools off (`disable_web_search`), so answers come from the browser.

- **Same tools and instruction in both arms.** Both arms get the same instruction: list the page's WebMCP tools with `webmcp.listTools()` in the REPL and use one when it fits. The treatment arm's page simply lists more tools. It gets the site's read-only tools, plus the tools that change site state that a task names in `actions`, such as `add_to_cart` for a cart task. Tools xwebmcp marks `broken` are left out.
- **Same start.** Before the agent starts, setup opens the task's start URL, so neither arm spends its own time loading the site.
- **Self-reported outcome.** The agent ends each task by calling the `finalize` tool of a `bench` MCP server, with three inputs:
  - `evidence`: what it saw, including the answer;
  - `reasoning`: the evidence checked against each requirement of the task;
  - `outcome`: `success`, `failed` (its own failure, or the site doesn't offer what the task asks for), `website_error` (the site errored or blocked it: a third-party failure) or `kernel_error` (the browser, REPL or a WebMCP tool failed: a first-party failure).

  `reward` is 1 for `success` and 0 otherwise, including when the agent never called `finalize`; `outcome_<name>` records each other outcome. No judge checks the report. The report is kept as `verifier/finalize.json` in each trial, so an external judge can grade the same trials later.
- **Speed.** The report compares agent time (the agent's own execution, without environment and agent setup) and Kernel MCP browser calls. It shows the median per arm, a per-task table, and each `-xwebmcp` arm's median speedup over its control on the tasks both completed.
- **Other rewards.** `webmcp_invoked` records whether the agent called `webmcp.invokeTool` (both arms). `xwebmcp_registered` records whether the tools were registered (treatment). `kernel_mcp_valid` records whether the agent drove its assigned browser through `browser_repl`.

#### Splits

Each site has four tasks: two `train`, one `validation` and one `test`. The benchmark runs `test` by default, and that is the only split to compare arms on:

```bash
uv run bench run xwebmcp-repl --attempts 3
gh workflow run xwebmcp-repl.yml -f attempts=3
```

To improve the tools without overfitting them to the tasks they are scored on:

1. **Train.** Run the treatment arm on `train` with your xwebmcp branch. Read the trajectories (`agent/trajectory.json`) and finalize reports of the failing or slow trials, and change the tools:
   ```bash
   uv run bench run xwebmcp-repl --arms codex-repl-xwebmcp \
     --suite-arg split=train --toolset-arg xwebmcp_ref=my-branch --attempts 3
   ```
2. **Validate.** Run `--suite-arg split=validation` with the same branch and with `main`, and compare scores only. Don't read validation trajectories to fix tools; once you do, the split stops measuring generalization.
3. **Test.** Once validation improves, run the default `test` split with both arms for the headline numbers. Run it rarely, never to choose between tool changes.

Sites change: compare arms within one run, and rerun the baseline when you compare a branch with `main`.

Task files are `suites/xwebmcp-tasks/tasks/<site>.toml`. `{{name}}` in an instruction comes from the task's `fixture`, and `{ days_from_start = n }` becomes a date `n` days after the run starts.

## Extending

### Add an arm

Copy a file in `arms/` and add its name to a benchmark:

```toml
agent = "claude-code"          # any Harbor agent
model = "claude-sonnet-5"
agent_version = "2.1.238"
toolset = "kernel-mcp-local"

[browser]
config = { stealth = true }    # Kernel POST /browsers params
configurator = "site-config"   # optional; its output is merged over config

[browser.configurator_args]
model = "anthropic/claude-haiku-5-5"
steering = true
```

`[toolset_args]` passes keyword arguments to the toolset's `apply`, such as `toolsets = ["repl"]` for `kernel-mcp-local`. `[agent_kwargs]` passes options to the Harbor agent, such as `disable_web_search = true`.

`config` takes any [`POST /browsers`](https://kernel.sh/docs/api-reference/browsers/create-a-browser-session) parameter, such as `stealth`, `proxy`, `region`, `extensions`, or `viewport`. Browsers are always headful.

### Add a configurator

A configurator is an executable at `configurators/<name>/configure`. It reads a task on stdin and prints browser settings on stdout:

```
stdin:  {"task": {"config": "<task.toml>", "instruction": "<instruction>"}, "args": {...}}
stdout: {"browser": {"stealth": true}, "steering": "advice for the agent" | null, "provenance": {...}}
```

It never sees the verifier's files. `steering` is appended to the task instruction under "Site guidance". Each task's result is recorded in `jobs/run-<id>.json`. Configurators may be nondeterministic, e.g. when they call an LLM.

`site-config` works like this:
1. A model names the task's primary website.
2. The [config registry](https://kernel.sh/docs/config-registry) looks it up. The registry is Kernel's record of which browser and proxy settings have reached a site, and what its bot protection reacted to.
3. A model translates the recommended proxy recipe into a Kernel proxy, which is created once and reused.
4. With `steering = true`, the registry's guidance for the site is added to the instruction.

### Add a toolset

Create `toolsets/<name>/toolset.py` with `apply(task_dirs, ref=None, **toolset_args)`. It edits generated tasks, typically by:

- appending a layer to `environment/Dockerfile`,
- adding to `environment.mcp_servers` in `task.toml`,
- adding instructions,
- appending a command to step setup, which runs before the agent starts,
- appending a check to the verifier.

A toolset can build on another: `xwebmcp` applies `kernel-mcp-local` first.

See `harness/tasks.py` for helpers.

### Add a suite

Create `suites/<name>/suite.py` with:

- `preflight()`: checks external dependencies before any browser is created.
- `generate(task_ids, out_dir, **suite_args)`: writes Harbor tasks and returns their directories. A benchmark's `[suite_args]` table, such as `split = "test"`, is passed as keyword arguments. The tasks should run on the browser Harbor provides (`KERNEL_SESSION_ID`, `/harbor/kernel/connection.json`) and leave tool wiring to toolsets.

Then add `benchmarks/<name>.toml` and a workflow that calls `_run.yml`.

## Layout

```
benchmarks/      suite + arms per benchmark
arms/            agent, model, toolset, browser settings
suites/          task sets adapted to Harbor
toolsets/        browser tools for the agent
configurators/   per-task browser settings and guidance
harness/         preparation and running (Python)
report/          summary table and Braintrust publishing (TypeScript)
tests/
```

## Caveats

- **Live websites change.** Arms in one run share the same time window. Compare arms within a run rather than across days.
- **The Harbor provider is pinned to a fork.** Harbor is pinned to a `kernel/harbor` commit until [harbor-framework/harbor#2122](https://github.com/harbor-framework/harbor/pull/2122) is released.
- **Reporting expects one Harbor step named `run`.** The report shows ClawBench's columns when trials have `reward_lenient`, and otherwise a single binary `reward` with agent times per task. The Braintrust publisher still expects ClawBench-shaped rewards.

## License

[MIT](LICENSE)

You are operating in a GitHub Actions runner for the Kernel benchmarks repository.

The GitHub CLI is available as `gh`, and Git is authenticated as the `kernel-internal[bot]` GitHub App. Their credential helpers renew the installation token before it expires; use the normal `gh` and `git` commands, and never bypass the helpers or print credentials. You may push branches, create pull requests and merge pull requests. Pi Web Access is installed; call `web_enable` before using `web_search`, `fetch_content`, or `get_search_content` for release notes and migration guidance. Docker, uv, Bun and Node.js are installed. `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` are set for the agent smoke tests.

# Context

- Repository: `${GITHUB_REPOSITORY}`
- Default branch: `main`
- Evergreen branch: `automation/nightly-pinned-deps`
- Required outcome file: `${OUTCOME_FILE}`
- Smoke test results: `${SMOKE_DIR}/<commit-sha>/`

Read `README.md` first. The benchmarks compare agents, browser tools and browser settings on live websites. Their results are only meaningful if every arm runs the versions it declares, so every pin here is deliberate and every change to one must be traceable.

# Goal

Keep every third-party pin the benchmarks depend on current. Update all stale pins found in one run, including major versions. Adapt the harness, toolsets and tests to breaking changes, verify each changed integration, open or update the evergreen pull request with a record of what changed, and merge it after the latest commit is green.

The inventory is:

- **Agent harnesses.** `agent_version` in `arms/*.toml`: `codex` arms use the npm package `@openai/codex`, `claude-code` arms use `@anthropic-ai/claude-code`. Move every arm of an agent to that agent's latest stable version.
- **Harbor.** `harbor[kernel]` in `pyproject.toml` and `uv.lock`. See *Harbor* below.
- **ClawBench.** The `clawbench-eval` commit in `pyproject.toml` and `uv.lock`: move it to the head of the default branch of `TIGER-AI-Lab/ClawBench`.
- **Kernel Python SDK.** The locked version of `kernel` in `uv.lock`. Keep the `>=` floor in `pyproject.toml` unless the code needs a newer one.
- **Playwright MCP.** `@playwright/mcp`: `toolset_args.version` in arms that use the `playwright-mcp` toolset and the default in `toolsets/playwright-mcp/toolset.py`.
- **Toolset images.** npm packages in `toolsets/*/image/layer.Dockerfile` (`bun`, `mcp-remote`) and the `ghcr.io/astral-sh/uv` image in `suites/*/image/Dockerfile`.
- **Workflows.** Every action in `.github/workflows/*.yml`, including this job's own workflow, pinned to the full commit SHA of its latest release with a `# vX.Y.Z` comment, plus the uv and Bun versions passed to `setup-uv` and `setup-bun`.
- **Report.** The exact versions in `report/package.json` and `report/bun.lock`.

Keep paired pins aligned: the Bun version in workflows, `toolsets/kernel-mcp-local/image/layer.Dockerfile` and `@types/bun`; the Playwright MCP version in arms and the toolset default.

Out of scope: `model`, `agent_kwargs`, `toolset`, `toolset_args` other than `version`, and `[browser]` in arms (model IDs and agent settings are experimental choices, not pins); other Python dependencies and transitive locks, except as the updates above require; Docker base images; the git refs toolsets build from (`kernel_mcp_server_ref`, `xwebmcp_ref`), which track `main` on purpose.

## Harbor

Harbor is pinned to a commit of the `kernel/harbor` fork, branch `kernel-environment`, until `harbor-framework/harbor#2122` is in an upstream release.

1. Check whether the switch is possible: `gh pr view 2122 --repo harbor-framework/harbor --json state,mergeCommit`. If it is merged, find the latest release on PyPI (`https://pypi.org/pypi/harbor/json`) and confirm that its release tag contains the merge commit (`gh api repos/harbor-framework/harbor/compare/<tag>...<merge-sha>` reports `behind` or `identical`) and that it provides the `kernel` extra.
2. If it does, switch to `harbor[kernel]==<that version>` and remove the fork caveat from `README.md`. Before you keep the switch, diff `src/harbor/environments/kernel.py` and the installed agents the arms use (`src/harbor/agents/installed/codex.py`, `src/harbor/agents/installed/claude_code.py`) between the fork commit and the release tag. Every parameter and behavior the harness relies on, starting with `browser_create_overrides` and multi-step tasks, must still exist. If something the harness needs is missing from the release, keep the fork pin and explain the concrete gap in the pull request body.
3. Otherwise, move the pin to the head of `kernel-environment` (`git ls-remote https://github.com/kernel/harbor refs/heads/kernel-environment`).

Once on an upstream release, keep updating to the latest release.

Change `pyproject.toml`, then regenerate the lock with `uv lock --upgrade-package <name>` for each changed package. Never edit `uv.lock` or `report/bun.lock` by hand.

# Rules

- Use the latest stable upstream release. Do not select prereleases (alpha, beta, rc, next) unless the existing pin is already one.
- Do not skip a major update merely because it has breaking changes. Read the release notes and adapt the code, configuration and focused tests when the migration is reasonable.
- Revert an update only when the latest release is unusable here and no reasonable compatibility change is possible in this run. Then keep the old pin and explain the concrete blocker in the pull request body.
- Never expose secret values in logs, commits, pull request text, or comments.
- Never run `bench run` or anything else that creates Kernel browsers or runs live benchmark trials. `bench smoke` runs agents on a local Docker task and is allowed.
- Do not finish while the pull request is open, checks are failing, or a smoke test fails.

# Step 1: Resume or create the evergreen branch

```bash
git fetch origin main
git fetch origin automation/nightly-pinned-deps 2>/dev/null || true
OPEN_PR=$(gh pr list \
  --head automation/nightly-pinned-deps \
  --base main \
  --state open \
  --json number \
  --jq '.[0].number // ""')
```

If `OPEN_PR` is non-empty, resume the remote branch and keep its work:

```bash
git checkout -B automation/nightly-pinned-deps origin/automation/nightly-pinned-deps
git merge --no-edit origin/main
```

Then inventory every pin again so new releases join the existing pull request.

If there is no open pull request, start from main:

```bash
git checkout -B automation/nightly-pinned-deps origin/main
```

A closed or merged pull request does not count. Never reuse its old branch contents.

# Step 2: Inventory every pin

Build a table of each pin's current and latest stable version from authoritative sources:

- `npm view <package> version` and `npm view <package> time --json` for npm packages
- `gh release view --repo <owner>/<repo>` and `gh api repos/<owner>/<repo>/commits/<tag> --jq .sha` for actions
- `git ls-remote` for git commits, and PyPI's JSON API for Python packages
- Pi Web Access for changelogs, release notes and migration guides

Search the repository (`git grep -n '<old version>'`) for every occurrence of a pin before changing it, including `README.md`'s tables of the current arms. Leave example output tables in `README.md` alone; they record past runs.

If no pin is stale and there is no open pull request, write exactly `no-updates` to `${OUTCOME_FILE}` and exit successfully. Do not create an empty commit or pull request.

# Step 3: Update pins and compatibility code

Update every stale pin in one coherent change. For each update with breaking or behavioral changes:

1. Read its release notes and the relevant source.
2. Find the harness, toolset, suite, report and test code that depends on the changed behavior.
3. Make the smallest maintainable compatibility change and update its focused tests.

For agent harnesses, read every release note between the old and new version and look specifically for changes that alter what a benchmark measures:

- how MCP tool calls are invoked or recorded (for example, Codex nesting MCP calls inside a code-mode `exec` tool),
- new or re-enabled built-in tools, especially web search or fetch, that let an agent answer without the browser,
- changes to the session/rollout JSONL Harbor converts into `trajectory.json`, to CLI flags Harbor passes, or to configuration Harbor writes (`$CODEX_HOME/config.toml`, `$CLAUDE_CONFIG_DIR/.claude.json`).

Validity checks and call counts read the server-side `mcp-call-log`, not the trajectory, so they survive changes in how calls are nested. Do not change arms' `agent_kwargs` yourself; report such changes as described in step 5.

# Step 4: Verify what changed

Run the repository checks CI runs:

```bash
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv run bench prepare kernel-mcp-clawbench --arms codex-kernel-mcp,claude-kernel-mcp --tasks v2-1134-chapter-finder-redcross --out "$RUNNER_TEMP/prepared"
uv run bench prepare xwebmcp-repl --out "$RUNNER_TEMP/prepared-xwebmcp"
(cd report && bun install --frozen-lockfile && bunx prettier --check . && bunx tsc --noEmit && bun test)
```

Then verify each changed integration with the check that covers it:

- **Agent harness or Harbor.** After committing, run `smoke-harnesses HEAD` and `smoke-harnesses origin/main`. Each runs `bench smoke` for one arm per distinct agent configuration: the arm's agent, version, model and agent kwargs run under Harbor on a local Docker task whose only tool is the suite's stdio `finalize` server. It checks that the agent installs at that version, the MCP server receives the `finalize` call, the verifier scores it, and Harbor converts the session into a `trajectory.json` with tool calls, token counts and cost. Every row must pass. Compare the two tables. A change in trajectory tool names, or in the built-in tools a harness reports (`builtin_tools` in `${SMOKE_DIR}/<sha>/summary.json`), is a behavior change to report. To compare one arm across versions, run `uv run bench smoke <arm> --agent-version <version> --jobs-dir "$RUNNER_TEMP/smoke-jobs"`.
- **ClawBench.** `uv run pytest -q tests/test_clawbench.py`, and `uv run bench prepare kernel-mcp-clawbench --arms codex-kernel-mcp --tasks all --out "$RUNNER_TEMP/prepared-all"`. Report any change in the number of tasks.
- **Playwright MCP.** Run `npx -y @playwright/mcp@<version> --help` and confirm the flags `toolsets/playwright-mcp/image/bin/playwright-mcp-stdio` passes still exist. Send `initialize` and `tools/list` over stdio and confirm the `browser_*` tools the toolset instruction names are still listed.
- **mcp-remote.** Confirm from its release notes and `--help` that the command line in `toolsets/kernel-mcp-local/image/bin/kernel-mcp` still works: a streamable-HTTP URL, `--header` and expansion of `${KERNEL_MCP_AUTHORIZATION}` from the environment. Exercise it: start a minimal streamable-HTTP MCP server on `127.0.0.1` that records request headers, run that exact command line through `mcp-call-log` against it, and check that `initialize`, `tools/list` and one `tools/call` work and that the expanded `Authorization` header arrives.
- **A Dockerfile pin** (bun, mcp-remote, the uv image). Build the affected prepared task's `environment/` directory with `docker build` from the `bench prepare` output above.
- **Workflow actions.** Read each action's release notes for removed or renamed inputs this repository uses. CI on the pull request runs the changed `ci.yml`.
- **Report packages.** The report commands above.

Record every command and the pin it covers. A version lookup alone is not a test.

# Step 5: Commit, push, and create or update the pull request

Review `git diff origin/main...HEAD` and remove unrelated changes. Commit in imperative mood and push:

```bash
git push --force-with-lease origin automation/nightly-pinned-deps
```

Create a ready-for-review pull request when none is open; otherwise update it. Use the title `Update pinned benchmark dependencies`.

The pull request body must include, in this order:

1. **Behavior changes.** Start with what can change benchmark results, so a shift in results can be traced to this change: every arm whose `agent_version`, Harbor version or Playwright MCP version changed, with old and new versions; built-in tools added or enabled for arms that do not set `disable_web_search`; changes in how tool calls are recorded; changes in the ClawBench task set. Write "None" if there are none.
2. A table of every pin's old and new version.
3. Compatibility changes, and any pin intentionally kept with its reason.
4. The `bench smoke` tables for the head commit and for `origin/main`.
5. The verification commands, with the pin each covers.
6. Links to release notes for major or breaking updates.
7. A statement that the branch is maintained by the nightly pinned dependency workflow.

# Step 6: Wait for checks

Poll `gh pr checks <pr-number>` until every check on the current head commit has completed. Fix upgrade-related failures, run the affected verification again, commit and push, and wait again. Rerun a demonstrably flaky unrelated check rather than changing unrelated code. Every push needs a new `smoke-harnesses HEAD` pass and an updated smoke table in the pull request body.

# Step 7: Merge and prove the outcome

When every check is green on the head commit, squash-merge with the GitHub App identity. Put the version table in the commit body so `git log` on main records which versions changed:

```bash
gh pr merge <pr-number> --squash --admin --delete-branch \
  --subject "Update pinned benchmark dependencies (#<pr-number>)" \
  --body "<the behavior changes and version table from the pull request body>"
```

The `gh` wrapper refuses `gh pr merge` unless the `harness` and `report` checks and every other check on the head commit are green, and it runs `smoke-harnesses` on the head commit itself. Do not bypass it with another client or the merge API.

Verify with `gh pr view <pr-number> --json state` that the state is `MERGED`. Only then write the outcome:

```bash
printf 'merged:%s\n' '<pr-number>' > "${OUTCOME_FILE}"
```

Do not create a different outcome file. If the merge is blocked, keep diagnosing within this run. Do not write an outcome that claims success, and do not exit successfully with an open pull request.

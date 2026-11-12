import { afterEach, expect, test } from "bun:test";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { readBenchmarkArm, summarizeArm } from "./results";
import {
  publicWebStats,
  reviewFlags,
  summarizePairs,
  summarizePublicWeb,
} from "./public-web";
import { signTest, wilson } from "./stats";
import { renderMarkdown } from "./report";
import { buildExperimentEvents } from "./publish-braintrust";

const dirs: string[] = [];
afterEach(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true });
});

function fixture() {
  const dir = mkdtempSync(join(tmpdir(), "public-web-report-"));
  dirs.push(dir);
  const json = (path: string, data: unknown) => {
    mkdirSync(join(path, ".."), { recursive: true });
    writeFileSync(path, JSON.stringify(data));
  };
  json(join(dir, "config.json"), { job_name: "public-web" });
  json(join(dir, "result.json"), {
    n_total_trials: 4,
    stats: { n_completed_trials: 2, n_errored_trials: 1 },
  });
  for (const [index, reward, duration] of [
    [1, 1, 20],
    [2, 0, 40],
    [3, 0, 0],
  ]) {
    json(join(dir, `trial-${index}/result.json`), {
      id: `trial-${index}`,
      task_name: "public-web/example",
      source: "public-web",
      config: {
        agent: {
          name: "codex",
          model_name: "model",
          kwargs: { version: "pinned" },
        },
      },
      verifier_result: {
        rewards: {
          reward,
          finalized: 1,
          page_verified: reward,
          webmcp_invoked: 1,
          kernel_mcp_valid: index === 2 ? 0 : 1,
          call_rejected: index === 1 ? 1 : 0,
          outcome_blocked: index === 2 ? 1 : 0,
          page_unverifiable: 0,
        },
      },
      exception_info: index === 3 ? { type: "SetupError" } : undefined,
      started_at: "2026-01-01T00:00:00Z",
      finished_at: "2026-01-01T00:10:00Z",
      step_results:
        index === 3
          ? []
          : [
              {
                agent_execution: {
                  started_at: "2026-01-01T00:01:00Z",
                  finished_at: `2026-01-01T00:01:${duration}Z`,
                },
                agent_result: {
                  n_input_tokens: 1000,
                  n_cache_tokens: 200,
                  n_output_tokens: 100,
                  cost_usd: index === 1 ? 0.25 : undefined,
                },
              },
            ],
    });
    json(join(dir, `trial-${index}/steps/run/verifier/source.json`), {
      sha: "a".repeat(40),
      grading: "end state",
    });
    json(
      join(dir, `trial-${index}/steps/run/verifier/kernel-mcp-result.json`),
      {
        browser_calls: index === 3 ? 0 : 7,
        kernel_mcp_server_sha: "b".repeat(40),
      },
    );
  }
  return dir;
}

test("public-web reports agent time, tokens, graded outcomes and pricing coverage", () => {
  const arm = readBenchmarkArm({ name: "control", path: fixture() });
  const stats = publicWebStats(arm.trials);
  expect(stats).toMatchObject({
    recorded: 3,
    passes: 1,
    passesByGrading: [{ grading: "end state", passes: 1, recorded: 3 }],
    pageVerified: 1,
    toolsetChecked: 3,
    toolsetValid: 2,
    rejectedCallTrials: 1,
    checked: { passes: 1, recorded: 3 },
    formatOnly: { passes: 0, recorded: 0 },
    stops: [
      { reason: "blocked", count: 1 },
      { reason: "errored (SetupError)", count: 1 },
    ],
    medianCalls: 7,
    medianAgentMs: 30000,
    medianPassedAgentMs: 20000,
    medianInputTokens: 1000,
    medianCacheTokens: 200,
    medianOutputTokens: 100,
    recordedCostUsd: 0.25,
    pricedTrials: 1,
  });
  // Browser calls come from the proxy's log; the trajectory's own count is left as it is.
  const first = arm.trials.find((trial) => trial.id === "trial-1")?.metrics;
  expect(first?.browserCalls).toBe(7);
  expect(first?.toolCalls).toBeUndefined();
  expect(
    arm.trials.find((trial) => trial.id === "trial-1")?.sourceRevision,
  ).toBe("a".repeat(40));
  expect(
    buildExperimentEvents([arm], "public-web-test").some(
      (event) =>
        event.metadata?.sourceRevision === "a".repeat(40) &&
        event.metadata?.taskGrading === "end state",
    ),
  ).toBe(true);
  const summaries = summarizePublicWeb([arm]);
  expect(summaries[0].intended).toBe(4);
  expect(summaries[0].tasks[0]).toMatchObject({
    task: "example",
    recorded: 3,
    passes: 1,
    grading: ["end state"],
  });
  const report = renderMarkdown(
    "public-web",
    [summarizeArm(arm)],
    undefined,
    {},
    [arm],
  );
  // The headline is the checked pass rate with its interval, beside recorded/intended.
  expect(report).toContain(
    "| 3/4 | 1/3 (6–79%) | 0/0 | end state 1/3 | blocked 1 · errored (SetupError) 1 | 0 |",
  );
  expect(report).toContain(
    "| control | 3 | 2/3 | 1 | 0 / 0 | 0 / 0 | 0 | 30s |",
  );
  expect(report).toContain(
    "| 1/3 (6–79%) | blocked 1 · errored (SetupError) 1 | 30s |",
  );
  expect(report).toContain("30s | 20s");
  expect(report).toContain("1000 / 200 / 100");
  expect(report).toContain("$0.2500 (1/3)");
  expect(report).toContain("not matched pairs");
  expect(report).toContain("Incomplete benchmark");
  expect(report).not.toContain("Lenient");
  expect(report).toContain("a".repeat(40));
});

test("all three arms render independently without pooling their attempts", () => {
  const dir = fixture();
  const arms = ["control", "treatment", "playwright"].map((name) =>
    readBenchmarkArm({ name, path: dir }),
  );
  const summaries = summarizePublicWeb(arms);
  expect(summaries.map((s) => [s.arm, s.passes, s.intended])).toEqual([
    ["control", 1, 4],
    ["treatment", 1, 4],
    ["playwright", 1, 4],
  ]);
  const report = renderMarkdown(
    "public-web",
    arms.map(summarizeArm),
    undefined,
    {},
    arms,
  );
  for (const name of ["control", "treatment", "playwright"])
    expect(report).toContain(`| ${name} |`);
  // A trial that errored before grading takes the grading of its task's other trials.
  const mixed = arms[0].trials.map((trial) =>
    trial.id === "trial-1"
      ? trial
      : {
          ...trial,
          taskName: "search",
          taskGrading: trial.error ? undefined : "shape/range rules",
        },
  );
  expect(publicWebStats(mixed)).toMatchObject({
    passesByGrading: [
      { grading: "end state", passes: 1, recorded: 1 },
      { grading: "shape/range rules", passes: 0, recorded: 2 },
    ],
    checked: { passes: 1, recorded: 1 },
    formatOnly: { passes: 0, recorded: 2 },
  });
  const twoTasks = arms.map((arm, index) => ({
    ...arm,
    trials: arm.trials.map((trial) => ({
      ...trial,
      taskName: index === 1 ? "another" : trial.taskName,
    })),
  }));
  const perTask = renderMarkdown(
    "public-web",
    twoTasks.map(summarizeArm),
    undefined,
    {},
    twoTasks,
  )
    .split("### Per task")[1]
    .split("\n")
    .flatMap((line) => line.match(/^\| (\w+) \| (\w+) \|/)?.slice(1, 3) ?? []);
  expect(perTask).toEqual([
    "Task",
    "Arm",
    "another",
    "treatment",
    "example",
    "control",
    "example",
    "playwright",
  ]);
  expect(publicWebStats([])).toMatchObject({
    recorded: 0,
    passes: 0,
    passesByGrading: [],
    recordedCostUsd: undefined,
    medianAgentMs: undefined,
  });
});

interface TrialSpec {
  task?: string;
  reward?: number;
  seconds?: number;
  pair?: string;
  first?: string;
  rewards?: Record<string, number>;
  stepError?: string;
  cost?: number;
  source?: Record<string, unknown>;
  answer?: unknown;
  calls?: { function_name: string; arguments: unknown }[];
  toolsets?: string[];
}

// A Harbor job directory per arm, one trial directory per spec.
function job(specs: TrialSpec[]) {
  const dir = mkdtempSync(join(tmpdir(), "public-web-pairs-"));
  dirs.push(dir);
  const json = (path: string, data: unknown) => {
    mkdirSync(join(path, ".."), { recursive: true });
    writeFileSync(path, JSON.stringify(data));
  };
  json(join(dir, "config.json"), { job_name: "public-web" });
  json(join(dir, "result.json"), { n_total_trials: specs.length, stats: {} });
  specs.forEach((spec, index) => {
    const trial = join(dir, `trial-${index}`);
    const rewards = {
      reward: spec.reward ?? 1,
      finalized: spec.stepError ? 0 : 1,
      ...spec.rewards,
    };
    json(join(trial, "result.json"), {
      id: `trial-${index}`,
      trial_name: `trial-${index}`,
      task_name: `public-web/${spec.task ?? "search"}`,
      source: "public-web",
      config: { agent: { name: "codex", model_name: "model" } },
      verifier_result: { rewards },
      step_results: [
        {
          exception_info: spec.stepError
            ? { exception_type: spec.stepError, exception_message: "failed" }
            : undefined,
          agent_execution: {
            started_at: "2026-01-01T00:00:00Z",
            finished_at: new Date(
              Date.UTC(2026, 0, 1) + (spec.seconds ?? 10) * 1000,
            ).toISOString(),
          },
          agent_result: { cost_usd: spec.cost },
        },
      ],
    });
    json(join(trial, "steps/run/verifier/source.json"), {
      sha: "a".repeat(40),
      grading: "pinned rules",
      domain: "example.com",
      stage: "find",
      limits: { browser_calls: 40, cost_usd: 2 },
      ...spec.source,
    });
    json(join(trial, "steps/run/verifier/grade.json"), { answer: spec.answer });
    json(join(trial, "steps/run/verifier/kernel-mcp-result.json"), {
      enabled_toolsets: spec.toolsets ?? ["playwright", "webmcp"],
    });
    if (spec.calls)
      json(join(trial, "steps/run/agent/trajectory.json"), {
        steps: [{ tool_calls: spec.calls }],
      });
    if (spec.pair)
      json(join(trial, "pair.json"), {
        id: spec.pair,
        baseline: "control",
        candidate: "treatment",
        first: spec.first ?? "control",
      });
  });
  return dir;
}

test("a model provider failure is an errored trial, not a failed task", () => {
  const arm = readBenchmarkArm({
    name: "control",
    path: job([
      {},
      { reward: 0, stepError: "AgentAuthenticationError" },
      { reward: 0, stepError: "AgentTimeoutError" },
    ]),
  });
  const byId = [...arm.trials].sort((a, b) => a.id.localeCompare(b.id));
  expect(byId.map((trial) => trial.errorType)).toEqual([
    undefined,
    "AgentAuthenticationError",
    undefined,
  ]);
  expect(publicWebStats(arm.trials).stops).toEqual([
    { reason: "errored (AgentAuthenticationError)", count: 1 },
  ]);
  const report = renderMarkdown(
    "public-web",
    [summarizeArm(arm)],
    undefined,
    { control: 0 },
    [arm],
  );
  expect(report).toContain("Incomplete benchmark");
  expect(report).toContain("had 1 infrastructure failure");
});

test("spend over the task's limit and a used-up call cap are reported, and only overspending fails a pass", () => {
  const arm = readBenchmarkArm({
    name: "control",
    path: job([
      { cost: 2.5 },
      { cost: 1.5, rewards: { call_cap_hit: 1 } },
      { reward: 0, rewards: { call_cap_hit: 1 } },
    ]),
  });
  expect(publicWebStats(arm.trials)).toMatchObject({
    passes: 1,
    stops: [
      { reason: "call cap", count: 1 },
      { reason: "over spend limit", count: 1 },
    ],
  });
});

test("tool use separates injected from native tools and tries from successes", () => {
  const arm = readBenchmarkArm({
    name: "treatment",
    path: job([
      { rewards: { injected_tool_invoked: 1, injected_tool_completed: 1 } },
      { rewards: { injected_tool_invoked: 1, native_tool_invoked: 1 } },
      { rewards: { webmcp_invoked_from_code: 1 } },
    ]),
  });
  expect(publicWebStats(arm.trials)).toMatchObject({
    injectedTool: { completed: 1, attempted: 2 },
    nativeTool: { completed: 0, attempted: 1 },
    invokedFromCode: 1,
  });
});

test("trials that reach the site outside the browser tools are flagged for review", () => {
  const browser = (code: string) => ({
    function_name: "mcp__kernel__execute_playwright_code",
    arguments: { session_id: "s", code },
  });
  const shell = (command: string) => ({
    function_name: "shell",
    arguments: { command },
  });
  const arm = readBenchmarkArm({
    name: "playwright",
    path: job([
      {
        calls: [
          shell("cat /run/kernel-mcp/session-id"),
          browser("await page.goto('https://example.com'); return 1"),
        ],
      },
      { calls: [shell("curl -s https://api.example.com/items")] },
      { calls: [shell("cat /opt/bench/start.json; echo $KERNEL_API_KEY")] },
      {
        toolsets: ["playwright"],
        calls: [browser("return await (await fetch('/api')).json()")],
      },
      // Codex runs its tools from a script: browser tools there are browser calls, a shell is not.
      {
        calls: [
          {
            function_name: "exec",
            arguments: {
              input:
                'const r=await tools.mcp__kernel__execute_playwright_code({session_id:"s",code:"await page.goto(\'https://example.com/a\')"});\ntext(r);',
            },
          },
          {
            function_name: "exec",
            arguments: {
              input:
                'text(await tools.exec_command({cmd:"cat /run/kernel-mcp/session-id"}));',
            },
          },
        ],
      },
      {
        calls: [
          {
            function_name: "exec",
            arguments: {
              input:
                'text(await tools.exec_command({cmd:"curl https://example.com/a"}));',
            },
          },
        ],
      },
      // Unlabelled MCP calls are still browser calls, and other arms may fetch from the page.
      {
        calls: [
          {
            function_name: "mcp_tool_call",
            arguments: {
              session_id: "s",
              code: "await fetch('https://example.com')",
            },
          },
        ],
      },
    ]),
  });
  const byId = [...arm.trials].sort((a, b) => a.id.localeCompare(b.id));
  expect(byId.map(reviewFlags)).toEqual([
    [],
    [
      "HTTP client outside the browser tools",
      "task site named outside the browser tools",
    ],
    [
      "Kernel API or key outside the browser tools",
      "task or verifier files read",
    ],
    ["HTTP request from page code"],
    [],
    [
      "HTTP client outside the browser tools",
      "task site named outside the browser tools",
    ],
    [],
  ]);
  const report = renderMarkdown(
    "public-web",
    [summarizeArm(arm)],
    undefined,
    {},
    [arm],
  );
  expect(report).toContain("- playwright trial-1: HTTP client outside");
  expect(publicWebStats(arm.trials).flagged).toHaveLength(4);
});

test("matched pairs compare speed over both-pass pairs and passes over discordant ones", () => {
  const price = { agreement: { path: "price", better: "lower", tolerance: 1 } };
  const gap = { stage: "gap", grading: "shape/range rules" };
  const control = readBenchmarkArm({
    name: "control",
    path: job([
      { pair: "search#1", seconds: 40, source: price, answer: { price: 100 } },
      { pair: "search#2", seconds: 30, source: price, answer: { price: 100 } },
      { pair: "search#3", seconds: 50, reward: 0, source: price },
      { pair: "search#4", seconds: 20, source: price },
      { pair: "search#5", seconds: 20, source: price },
      { pair: "release#1", task: "release", seconds: 20, source: gap },
    ]),
  });
  const treatment = readBenchmarkArm({
    name: "treatment",
    path: job([
      {
        pair: "search#1",
        first: "treatment",
        seconds: 10,
        source: price,
        answer: { price: 120 },
        rewards: { injected_tool_completed: 1 },
      },
      {
        pair: "search#2",
        seconds: 20,
        source: price,
        answer: { price: 100.5 },
      },
      { pair: "search#3", seconds: 10, source: price },
      {
        pair: "search#4",
        reward: 0,
        stepError: "ApiRateLimitError",
        source: price,
      },
      // No control trial has this pair, so it is not counted.
      { pair: "search#9", seconds: 5, source: price },
      { pair: "release#1", task: "release", seconds: 25, source: gap },
    ]),
  });
  const [pairs] = summarizePairs([control, treatment]);
  expect(pairs).toMatchObject({ baseline: "control", candidate: "treatment" });
  expect(pairs.tasks.map((task) => [task.task, task.placebo])).toEqual([
    ["release", true],
    ["search", false],
  ]);
  // search#4 errored in treatment and search#5 has no partner, so three pairs are complete.
  expect(pairs.tasks[1]).toMatchObject({
    pairs: 3,
    both: 2,
    onlyBaseline: 0,
    onlyCandidate: 1,
    passP: 1,
    baselineMedianMs: 35000,
    candidateMedianMs: 15000,
    speedup: 2.75,
    faster: 2,
    slower: 0,
    speedP: 0.5,
    agreement: { checked: 2, candidateWorse: 1, baselineWorse: 0 },
    injectedCompleted: 1,
    candidateFirst: 1,
  });
  // The pooled row leaves the placebo out.
  expect(pairs.covered).toMatchObject({ pairs: 3, both: 2 });
  expect(pairs.tasks[0]).toMatchObject({ pairs: 1, faster: 0, slower: 1 });

  const report = renderMarkdown(
    "public-web",
    [control, treatment].map(summarizeArm),
    undefined,
    {},
    [control, treatment],
  );
  expect(report).toContain("### Matched pairs: control vs treatment");
  expect(report).toContain(
    "| search | 3 | 2 | 0 / 1 (p=1.00) | 35s → 15s | 2.75× | 2 / 0 (p=0.50) | control worse 0 · treatment worse 1 of 2 | 1/3 |",
  );
  expect(report).toContain("| release (placebo) | 1 | 1 |");
  expect(report).toContain("| All tool-covered tasks | 3 | 2 |");
  expect(report).not.toContain("not matched pairs");

  const same = { agreement: { path: "games[].game", same: true } };
  const games = (...names: string[]) => ({
    games: names.map((game) => ({ game })),
  });
  const [compared] = summarizePairs([
    readBenchmarkArm({
      name: "control",
      path: job([
        { pair: "a#1", source: same, answer: games("Civ V", "Civ VI™") },
        { pair: "a#2", source: same, answer: games("Civ V") },
      ]),
    }),
    readBenchmarkArm({
      name: "treatment",
      path: job([
        { pair: "a#1", source: same, answer: games("civ  vi", "CIV V") },
        { pair: "a#2", source: same, answer: games("Civ VII") },
      ]),
    }),
  ]);
  expect(compared.tasks[0].agreement).toEqual({ checked: 2, differed: 1 });
});

test("interval and sign-test statistics", () => {
  const [low, high] = wilson(5, 5) as [number, number];
  expect([low.toFixed(3), high.toFixed(3)]).toEqual(["0.566", "1.000"]);
  expect(wilson(0, 0)).toBeUndefined();
  expect(signTest(5, 5)).toBeCloseTo(0.0625);
  expect(signTest(4, 5)).toBeCloseTo(0.375);
  expect(signTest(0, 0)).toBeUndefined();
});

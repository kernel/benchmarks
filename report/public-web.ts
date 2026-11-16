import { join } from "node:path";
import {
  array,
  type BenchmarkArm,
  type BenchmarkTrial,
  median,
  number,
  object,
  overSpend,
  readJson,
  readJsonIfPresent,
  string,
  summarizeArm,
} from "./results";
import { signTest, wilson } from "./stats";

const passed = (trial: BenchmarkTrial) =>
  !trial.error && trial.rewards.reward === 1;
const values = (
  trials: BenchmarkTrial[],
  pick: (trial: BenchmarkTrial) => number | undefined,
) =>
  trials.flatMap((trial) => {
    const value = pick(trial);
    return value === undefined ? [] : [value];
  });
const agentMs = (trial: BenchmarkTrial) =>
  trial.phases.agentExecution?.durationMs;

// Strongest check first. A shape/range pass shows only a well-formed, in-range answer.
const GRADINGS = ["end state", "pinned rules", "shape/range rules"];
// Gradings that check the answer against the page or a pinned value.
const CHECKED = new Set(["end state", "pinned rules"]);
const gradingRank = (name: string) =>
  GRADINGS.includes(name) ? GRADINGS.indexOf(name) : GRADINGS.length;
// No tool covers a gap task, so it is the placebo for the injected tools.
const placebo = (trial: BenchmarkTrial) => trial.taskInfo.stage === "gap";

// A trial that failed before it was graded has no grading of its own, so it takes its task's.
function taskGradings(trials: BenchmarkTrial[]): Map<string, string> {
  return new Map(
    trials.flatMap((trial) =>
      trial.taskGrading ? [[trial.taskName, trial.taskGrading]] : [],
    ),
  );
}

// Why a trial did not pass, other than a wrong answer. The first three are the agent's own report.
const STOPS: [string, (trial: BenchmarkTrial) => boolean][] = [
  ["blocked", (trial) => trial.rewards.outcome_blocked === 1],
  ["site error", (trial) => trial.rewards.outcome_website_error === 1],
  ["Kernel error", (trial) => trial.rewards.outcome_kernel_error === 1],
  ["page unreadable", (trial) => trial.rewards.page_unverifiable === 1],
  ["call cap", (trial) => !passed(trial) && trial.rewards.call_cap_hit === 1],
  ["over spend limit", (trial) => !trial.error && overSpend(trial)],
];

function stopReasons(trials: BenchmarkTrial[]) {
  const errored = new Map<string, number>();
  for (const trial of trials.filter((trial) => trial.error !== undefined)) {
    const reason = trial.errorType ? `errored (${trial.errorType})` : "errored";
    errored.set(reason, (errored.get(reason) ?? 0) + 1);
  }
  return [
    ...STOPS.map(([reason, stopped]) => ({
      reason,
      count: trials.filter(stopped).length,
    })),
    ...[...errored].map(([reason, count]) => ({ reason, count })),
  ].filter((stop) => stop.count > 0);
}

// Tool calls that reach the website, the Kernel API or the task's files without going through
// the arm's browser tools. The call proxy sees only MCP calls, so these come from the
// agent's own trajectory. A flag asks for a look at the trial; it does not change its score.
const BROWSER_TOOL =
  /(?:execute_playwright_code|browser_repl|computer_action|webmcp|finalize)$/;
const OUTSIDE_BROWSER: [string, RegExp][] = [
  [
    "HTTP client outside the browser tools",
    /\b(?:curl|wget)\b|https?:\/\/(?!127\.0\.0\.1|localhost)/,
  ],
  [
    "Kernel API or key outside the browser tools",
    /api\.onkernel\.com|KERNEL_API_KEY|kernel-mcp\/api-key/,
  ],
  [
    "task or verifier files read",
    /\/opt\/bench|\/tests\b|start\.json|tools\.js/,
  ],
];
// Codex calls its tools from a script, so one call can hold several: tools.<name>(...).
const SCRIPTED_TOOL = /\btools\.(\w+)\s*\(/g;
const HTTP_IN_CODE =
  /\bfetch\s*\(|XMLHttpRequest|\b(?:page|context|browser)\.request\b|\brequest\.(?:get|post|fetch)\s*\(/;

export function reviewFlags(trial: BenchmarkTrial): string[] {
  if (!trial.trajectoryPath) return [];
  const domain = string(trial.taskInfo.domain)?.toLowerCase();
  const toolsets = array(
    readJsonIfPresent(
      join(trial.dir, "steps/run/verifier/kernel-mcp-result.json"),
    ).enabled_toolsets,
  );
  // Only the Playwright-only arm is told not to make HTTP requests from page code.
  const playwrightOnly = toolsets.length === 1 && toolsets[0] === "playwright";
  const flags = new Set<string>();
  for (const step of array(readJson(trial.trajectoryPath).steps)) {
    for (const call of array(object(step).tool_calls)) {
      const name = string(object(call).function_name) ?? "";
      const args = object(object(call).arguments);
      const text = JSON.stringify(args);
      const scripted = [...text.matchAll(SCRIPTED_TOOL)].map(
        (match) => match[1],
      );
      // Every browser tool takes the session ID, however the agent names its MCP tools.
      if (
        BROWSER_TOOL.test(name) ||
        "session_id" in args ||
        (scripted.length > 0 &&
          scripted.every((tool) => BROWSER_TOOL.test(tool)))
      ) {
        if (playwrightOnly && HTTP_IN_CODE.test(text))
          flags.add("HTTP request from page code");
        continue;
      }
      if (domain && text.toLowerCase().includes(domain))
        flags.add("task site named outside the browser tools");
      for (const [flag, pattern] of OUTSIDE_BROWSER)
        if (pattern.test(text)) flags.add(flag);
    }
  }
  return [...flags].sort();
}

interface ExpectedTrials {
  counts: Map<string, number>;
  total: number | null;
}

function passesByGrading(
  trials: BenchmarkTrial[],
  gradings: Map<string, string>,
  expected: ExpectedTrials,
) {
  const tasks = new Set([
    ...expected.counts.keys(),
    ...trials.map((trial) => trial.taskName),
  ]);
  const groups = new Map<
    string,
    { grading: string; passes: number; recorded: number; intended: number }
  >();
  for (const task of tasks) {
    const grading = gradings.get(task) ?? "unknown";
    const row = groups.get(grading) ?? {
      grading,
      passes: 0,
      recorded: 0,
      intended: 0,
    };
    const recorded = trials.filter((trial) => trial.taskName === task);
    row.passes += recorded.filter(passed).length;
    row.recorded += recorded.length;
    row.intended += expected.counts.get(task) ?? recorded.length;
    groups.set(grading, row);
  }
  const missing =
    (expected.total ?? 0) -
    [...groups.values()].reduce((sum, group) => sum + group.intended, 0);
  if (missing > 0) {
    const unknown = groups.get("unknown") ?? {
      grading: "unknown",
      passes: 0,
      recorded: 0,
      intended: 0,
    };
    unknown.intended += missing;
    groups.set("unknown", unknown);
  }
  return [...groups.values()]
    .sort((a, b) => gradingRank(a.grading) - gradingRank(b.grading))
    .map((group) => ({
      ...group,
      intended: expected.total === null || missing > 0 ? null : group.intended,
    }));
}

function passRate(groups: ReturnType<typeof passesByGrading>, known: boolean) {
  const passes = groups.reduce((sum, group) => sum + group.passes, 0);
  const intended = known
    ? groups.reduce((sum, group) => sum + (group.intended ?? 0), 0)
    : null;
  return {
    passes,
    recorded: groups.reduce((sum, group) => sum + group.recorded, 0),
    intended,
    interval: intended === null ? undefined : wilson(passes, intended),
  };
}

// Trials that invoked a tool of one kind, and those where an invocation succeeded.
function toolUse(trials: BenchmarkTrial[], kind: "injected" | "native") {
  return {
    completed: trials.filter(
      (trial) => trial.rewards[`${kind}_tool_completed`] === 1,
    ).length,
    attempted: trials.filter(
      (trial) => trial.rewards[`${kind}_tool_invoked`] === 1,
    ).length,
  };
}

export function publicWebStats(
  trials: BenchmarkTrial[],
  gradings: Map<string, string> = taskGradings(trials),
  expected: ExpectedTrials = { counts: new Map(), total: trials.length },
) {
  const costs = values(trials, (trial) => trial.metrics.costUsd);
  const byGrading = passesByGrading(trials, gradings, expected);
  const known =
    expected.total !== null &&
    !byGrading.some(
      (group) => group.intended === null || !GRADINGS.includes(group.grading),
    );
  const flagged = trials.flatMap((trial) => {
    const flags = reviewFlags(trial);
    return flags.length ? [{ trial: trial.trialName, flags }] : [];
  });
  return {
    recorded: trials.length,
    intended: expected.total,
    passes: trials.filter(passed).length,
    // The headline: passes on tasks whose answer is checked against the page or a pinned value.
    checked: passRate(
      byGrading.filter((group) => CHECKED.has(group.grading)),
      known,
    ),
    formatOnly: passRate(
      byGrading.filter((group) => group.grading === "shape/range rules"),
      known,
    ),
    interval:
      expected.total === null
        ? undefined
        : wilson(trials.filter(passed).length, expected.total),
    passesByGrading: byGrading,
    pageVerified: trials.filter(
      (trial) => passed(trial) && trial.rewards.page_verified === 1,
    ).length,
    finalized: trials.filter((trial) => trial.rewards.finalized === 1).length,
    toolsetChecked: trials.filter(
      (trial) => trial.rewards.kernel_mcp_valid !== undefined,
    ).length,
    toolsetValid: trials.filter((trial) => trial.rewards.kernel_mcp_valid === 1)
      .length,
    rejectedCallTrials: trials.filter(
      (trial) => trial.rewards.call_rejected === 1,
    ).length,
    adopted: trials.filter((trial) => trial.rewards.webmcp_invoked === 1)
      .length,
    injectedTool: toolUse(trials, "injected"),
    nativeTool: toolUse(trials, "native"),
    invokedFromCode: trials.filter(
      (trial) => trial.rewards.webmcp_invoked_from_code === 1,
    ).length,
    flagged,
    stops: stopReasons(trials),
    medianAgentMs: median(values(trials, agentMs)),
    medianPassedAgentMs: median(values(trials.filter(passed), agentMs)),
    medianCalls: median(values(trials, (trial) => trial.metrics.browserCalls)),
    medianInputTokens: median(
      values(trials, (trial) => trial.metrics.inputTokens),
    ),
    medianCacheTokens: median(
      values(trials, (trial) => trial.metrics.cacheTokens),
    ),
    medianOutputTokens: median(
      values(trials, (trial) => trial.metrics.outputTokens),
    ),
    recordedCostUsd: costs.length
      ? costs.reduce((a, b) => a + b, 0)
      : undefined,
    pricedTrials: costs.length,
  };
}

export function isPublicWeb(arms: BenchmarkArm[]): boolean {
  return arms.some(
    (arm) =>
      arm.trials.some((trial) => trial.source === "public-web") ||
      Object.values(arm.taskPlan).some((task) => task.source === "public-web"),
  );
}

export function summarizePublicWeb(arms: BenchmarkArm[]) {
  const gradings = taskGradings(arms.flatMap((arm) => arm.trials));
  for (const arm of arms) {
    for (const [task, plan] of Object.entries(arm.taskPlan)) {
      const grading = string(plan.metadata.grading);
      if (grading) gradings.set(task, grading);
    }
  }
  return arms.map((arm) => {
    const counts = new Map(
      Object.entries(arm.taskPlan).map(([task, plan]) => [task, plan.attempts]),
    );
    const tasks = [
      ...new Set([
        ...counts.keys(),
        ...arm.trials.map((trial) => trial.taskName),
      ]),
    ].sort();
    return {
      arm: arm.name,
      configuration: summarizeArm(arm).configuration,
      ...publicWebStats(arm.trials, gradings, {
        counts,
        total: arm.nTotalTrials,
      }),
      tasks: tasks.map((task) => {
        const trials = arm.trials.filter((trial) => trial.taskName === task);
        const total =
          counts.get(task) ??
          (arm.trials.length === arm.nTotalTrials ? trials.length : null);
        return {
          task,
          grading: gradings.has(task) ? [gradings.get(task) as string] : [],
          placebo:
            arm.taskPlan[task]?.metadata.stage === "gap" ||
            trials.some(placebo),
          ...publicWebStats(trials, gradings, {
            counts: new Map([[task, total ?? trials.length]]),
            total,
          }),
        };
      }),
    };
  });
}

// Values at a path such as "games[].price"; "[]" steps into every array item.
function resolvePath(value: unknown, path: string): unknown[] {
  let found = [value];
  for (const segment of path.split(".")) {
    const each = segment.endsWith("[]");
    const key = each ? segment.slice(0, -2) : segment;
    found = found.flatMap((item) => {
      const next = object(item)[key];
      if (next === undefined) return [];
      if (!each) return [next];
      return array(next);
    });
  }
  return found;
}

// Strings compare without case, spacing or the trademark symbols agents copy
// inconsistently, and objects regardless of key order.
const canonical = (value: unknown) =>
  JSON.stringify(value, (_key, item) => {
    if (typeof item === "string")
      return item
        .replace(/[™®℠©]/g, "")
        .trim()
        .toLowerCase()
        .replace(/\s+/g, " ");
    if (item && typeof item === "object" && !Array.isArray(item))
      return Object.fromEntries(
        Object.entries(item).sort(([a], [b]) => a.localeCompare(b)),
      );
    return item;
  });

interface Pair {
  baseline: BenchmarkTrial;
  candidate: BenchmarkTrial;
}

const answer = (trial: BenchmarkTrial) =>
  readJsonIfPresent(join(trial.dir, "steps/run/verifier/grade.json")).answer;

// A pair's trials run back to back, so when both pass but the best value differs, the worse
// arm missed it; with `same`, differing answers mean one arm was wrong, though not which.
function agreementStats(pairs: Pair[], rule: unknown) {
  const { path, better, tolerance, same } = object(rule);
  if (typeof path !== "string") return undefined;
  if (same) {
    const set = (trial: BenchmarkTrial) =>
      JSON.stringify(resolvePath(answer(trial), path).map(canonical).sort());
    return {
      checked: pairs.length,
      differed: pairs.filter(
        (pair) => set(pair.baseline) !== set(pair.candidate),
      ).length,
    };
  }
  const value = (trial: BenchmarkTrial) =>
    number(resolvePath(answer(trial), path)[0]);
  const gaps = pairs.flatMap((pair) => {
    const [base, other] = [value(pair.baseline), value(pair.candidate)];
    return base === undefined || other === undefined
      ? []
      : [(other - base) * (better === "lower" ? 1 : -1)];
  });
  const slack = number(tolerance) ?? 0;
  return {
    checked: gaps.length,
    candidateWorse: gaps.filter((gap) => gap > slack).length,
    baselineWorse: gaps.filter((gap) => gap < -slack).length,
  };
}

// Speed and its sign test use only pairs where both trials passed, so an answer whose
// partner timed out does not shorten its arm's median.
function pairStats(pairs: Pair[], agreement?: unknown) {
  const complete = pairs.filter(
    (pair) => !pair.baseline.error && !pair.candidate.error,
  );
  const both = complete.filter(
    (pair) => passed(pair.baseline) && passed(pair.candidate),
  );
  const timed = both.flatMap((pair) => {
    const [base, other] = [agentMs(pair.baseline), agentMs(pair.candidate)];
    return base === undefined || other === undefined || other === 0
      ? []
      : [{ base, other }];
  });
  const onlyCandidate = complete.filter(
    (pair) => passed(pair.candidate) && !passed(pair.baseline),
  ).length;
  const onlyBaseline = complete.filter(
    (pair) => passed(pair.baseline) && !passed(pair.candidate),
  ).length;
  const faster = timed.filter((time) => time.other < time.base).length;
  const slower = timed.filter((time) => time.other > time.base).length;
  return {
    pairs: complete.length,
    both: both.length,
    onlyBaseline,
    onlyCandidate,
    // An exact McNemar test over the pairs that passed in one arm only.
    passP: signTest(onlyCandidate, onlyCandidate + onlyBaseline),
    baselineMedianMs: median(timed.map((time) => time.base)),
    candidateMedianMs: median(timed.map((time) => time.other)),
    speedup: median(timed.map((time) => time.base / time.other)),
    faster,
    slower,
    speedP: signTest(faster, faster + slower),
    agreement: agreement ? agreementStats(both, agreement) : undefined,
    injectedCompleted: complete.filter(
      (pair) => pair.candidate.rewards.injected_tool_completed === 1,
    ).length,
    candidateFirst: complete.filter(
      (pair) => pair.candidate.pair?.first === pair.candidate.arm,
    ).length,
  };
}

// The matched pairs the harness ran, per baseline and candidate arm.
export function summarizePairs(arms: BenchmarkArm[]) {
  const byName = new Map(arms.map((arm) => [arm.name, arm]));
  const sets = new Map<string, { baseline: string; candidate: string }>();
  for (const trial of arms.flatMap((arm) => arm.trials)) {
    const [baseline, candidate] = [
      string(trial.pair?.baseline),
      string(trial.pair?.candidate),
    ];
    if (baseline && candidate && byName.has(baseline) && byName.has(candidate))
      sets.set(`${baseline}|${candidate}`, { baseline, candidate });
  }
  return [...sets.values()].map(({ baseline, candidate }) => {
    const partner = new Map(
      (byName.get(candidate) as BenchmarkArm).trials.map((trial) => [
        string(trial.pair?.id),
        trial,
      ]),
    );
    const pairs = (byName.get(baseline) as BenchmarkArm).trials.flatMap(
      (trial) => {
        const other = partner.get(string(trial.pair?.id));
        return trial.pair && other
          ? [{ baseline: trial, candidate: other }]
          : [];
      },
    );
    const tasks = [...new Set(pairs.map((pair) => pair.baseline.taskName))]
      .sort()
      .map((task) => {
        const ofTask = pairs.filter((pair) => pair.baseline.taskName === task);
        return {
          task,
          placebo: ofTask.some((pair) => placebo(pair.baseline)),
          ...pairStats(
            ofTask,
            ofTask.find((pair) => pair.baseline.taskInfo.agreement)?.baseline
              .taskInfo.agreement,
          ),
        };
      });
    return {
      baseline,
      candidate,
      tasks,
      covered: pairStats(pairs.filter((pair) => !placebo(pair.baseline))),
    };
  });
}

const count = (value?: number) =>
  value === undefined ? "—" : String(Math.round(value));
const seconds = (value?: number) =>
  value === undefined ? "—" : `${Math.round(value / 1000)}s`;
const percent = (value: number) => `${Math.round(value * 100)}`;
const rate = (value: {
  passes: number;
  intended: number | null;
  interval?: [number, number];
}) =>
  value.interval
    ? `${value.passes}/${value.intended} (${percent(value.interval[0])}–${percent(value.interval[1])}%)`
    : "—";
const pValue = (value?: number) =>
  value === undefined
    ? ""
    : value < 0.01
      ? " (p<0.01)"
      : ` (p=${value.toFixed(2)})`;
const stops = (list: { reason: string; count: number }[]) =>
  list.map((stop) => `${stop.reason} ${stop.count}`).join(" · ") || "—";
const table = (headers: string[], rows: (string | number)[][]) =>
  [
    `| ${headers.join(" | ")} |`,
    `|${headers.map(() => "---").join("|")}|`,
    ...rows.map((row) => `| ${row.join(" | ")} |`),
  ].join("\n");

function agreementText(
  agreement: ReturnType<typeof agreementStats>,
  baseline: string,
  candidate: string,
) {
  if (!agreement || agreement.checked === 0) return "—";
  return "differed" in agreement
    ? `differed in ${agreement.differed}/${agreement.checked}`
    : `${baseline} worse ${agreement.baselineWorse} · ${candidate} worse ${agreement.candidateWorse} of ${agreement.checked}`;
}

function renderPairs(arms: BenchmarkArm[]): string[] {
  return summarizePairs(arms).flatMap(
    ({ baseline, candidate, tasks, covered }) => {
      const row = (
        name: string,
        stats: ReturnType<typeof pairStats>,
      ): (string | number)[] => [
        name,
        stats.pairs,
        stats.both,
        `${stats.onlyBaseline} / ${stats.onlyCandidate}${pValue(stats.passP)}`,
        `${seconds(stats.baselineMedianMs)} → ${seconds(stats.candidateMedianMs)}`,
        stats.speedup === undefined ? "—" : `${stats.speedup.toFixed(2)}×`,
        `${stats.faster} / ${stats.slower}${pValue(stats.speedP)}`,
        agreementText(stats.agreement, baseline, candidate),
        `${stats.injectedCompleted}/${stats.pairs}`,
      ];
      return [
        "",
        `### Matched pairs: ${baseline} vs ${candidate}`,
        "",
        table(
          [
            "Task",
            "Complete pairs",
            "Both pass",
            `Only ${baseline} / only ${candidate} passed`,
            "Median agent time, both-pass pairs",
            "Median pair speedup",
            `${candidate} faster / slower`,
            "Answers",
            "Injected tool completed",
          ],
          [
            ...tasks.map((task) =>
              row(task.placebo ? `${task.task} (placebo)` : task.task, task),
            ),
            row("All tool-covered tasks", covered),
          ],
        ),
        "",
        `Each pair is one ${baseline} and one ${candidate} trial of the same task, run back to back in a random order balanced within each task; ${candidate} ran first in ${covered.candidateFirst} of the ${covered.pairs} complete tool-covered pairs. A pair is complete when neither trial errored. Speed compares only pairs where both trials passed: the speedup is the median over those pairs of ${baseline}'s agent time divided by ${candidate}'s, and its p-value is an exact two-sided sign test on which trial was faster. The pass p-value is an exact McNemar test over pairs that passed in one arm only. With 5 pairs a sign test reaches p < 0.1 only when all 5 go the same way, so read a single task as a screen. Answers compares the two answers of both-pass pairs where the task defines how. No tool covers a placebo task, so a difference there measures noise rather than the tools.`,
      ];
    },
  );
}

export function renderPublicWeb(arms: BenchmarkArm[]): string {
  const summaries = summarizePublicWeb(arms);
  const paired = renderPairs(arms);
  const flagged = summaries.flatMap((s) =>
    s.flagged.map(
      (entry) => `${s.arm} ${entry.trial}: ${entry.flags.join("; ")}`,
    ),
  );
  const lines = [
    table(
      [
        "Arm",
        "Agent / model",
        "Recorded / intended",
        "Checked pass (95% CI)",
        "Format-only pass",
        "Passes by grading",
        "Stopped by",
        "Flagged for review",
      ],
      summaries.map((s) => [
        s.arm,
        s.configuration ?? "unknown",
        `${s.recorded}/${s.intended}`,
        rate(s.checked),
        s.formatOnly.intended === null
          ? "—"
          : `${s.formatOnly.passes}/${s.formatOnly.intended}`,
        s.passesByGrading
          .map((g) => `${g.grading} ${g.passes}/${g.intended ?? "?"}`)
          .join(" · ") || "—",
        stops(s.stops),
        s.flagged.length,
      ]),
    ),
    "",
    table(
      [
        "Arm",
        "Finalized",
        "Toolset valid",
        "Rejected-call trials",
        "Injected tool completed / tried",
        "Native tool completed / tried",
        "WebMCP invoked from code",
        "Median agent time",
        "Median agent time (passes)",
        "Median browser calls",
        "Median input / cached / output tokens",
        "Recorded model cost (priced / recorded)",
      ],
      summaries.map((s) => [
        s.arm,
        s.finalized,
        `${s.toolsetValid}/${s.toolsetChecked}`,
        s.rejectedCallTrials,
        `${s.injectedTool.completed} / ${s.injectedTool.attempted}`,
        `${s.nativeTool.completed} / ${s.nativeTool.attempted}`,
        s.invokedFromCode,
        seconds(s.medianAgentMs),
        seconds(s.medianPassedAgentMs),
        count(s.medianCalls),
        `${count(s.medianInputTokens)} / ${count(s.medianCacheTokens)} / ${count(s.medianOutputTokens)}`,
        `${s.recordedCostUsd === undefined ? "—" : `$${s.recordedCostUsd.toFixed(4)}`} (${s.pricedTrials}/${s.recorded})`,
      ]),
    ),
    ...paired,
    "",
    "### Per task",
    "",
    table(
      [
        "Task",
        "Arm",
        "Grading",
        "Pass / intended (95% CI)",
        "Stopped by",
        "Median agent time",
        "Median agent time (passes)",
        "Median input / output tokens",
      ],
      summaries
        .flatMap((s) => s.tasks.map((t) => ({ arm: s.arm, ...t })))
        .sort((a, b) => a.task.localeCompare(b.task))
        .map((t) => [
          t.placebo ? `${t.task} (placebo)` : t.task,
          t.arm,
          t.grading.join(", ") || "unknown",
          rate({
            passes: t.passes,
            intended: t.intended,
            interval: t.interval,
          }),
          stops(t.stops),
          seconds(t.medianAgentMs),
          seconds(t.medianPassedAgentMs),
          `${count(t.medianInputTokens)} / ${count(t.medianOutputTokens)}`,
        ]),
    ),
    "",
    "Checked pass counts only tasks whose answer is checked against the page's end state or a pinned value, with a 95% Wilson interval over intended trials. Planned task gradings and attempt counts keep setup failures and missing trials in the denominator. Rates are unavailable when an older run lacks enough task-plan metadata to determine its denominators; unknown grading is never counted as format-only. A format-only pass, on a task with shape/range rules, shows a well-formed, in-range answer and nothing about whether it is true, and pinned-answer rules do not independently verify every returned fact. A pass also needs the agent to have used its assigned browser toolset and to have stayed within the task's spend limit. A rejected-call trial made a call its arm does not permit; the proxy blocked the call, which does not fail the trial.",
    "",
    "Stopped by counts trials that did not pass for a reason other than a wrong answer: blocked, site error and Kernel error are the agent's own report of what stopped it, page unreadable means the page could not be read for the end-state check, call cap means the task's browser-call limit ran out, over spend limit means the recorded model cost exceeded the task's limit, and errored trials never got a fair run, such as a failed setup or a model provider that rejected the agent's requests.",
    "",
    "An injected tool is one of the arm's xwebmcp tools and a native tool is the site's own; tried counts trials that invoked one through the webmcp tool, and completed those where an invocation succeeded. An invocation made from Playwright or REPL code names no tool the proxy can see, so those trials are counted apart. Flagged trials have a tool call in the agent's trajectory that reached the task's site, the Kernel API or the task's files outside the arm's browser tools; review them before publishing.",
    "",
    paired.length
      ? "Agent time excludes browser/container setup and verification. The tables above are per arm and descriptive; only the matched-pairs table compares two arms, and an arm outside it ran independently in the same window. Pass rates count intended trials, including errors and missing trials; Recorded / intended shows how many ran. Cost covers only priced trials, not browser costs or unreported model charges."
      : "Agent time excludes browser/container setup and verification. Arms run concurrently in the same window; repetitions are not matched pairs, so these tables make no paired speedup or significance claim. Pass rates count intended trials, including errors and missing trials; Recorded / intended shows how many ran. Cost covers only priced trials, not browser costs or unreported model charges.",
  ];
  if (flagged.length)
    lines.push(
      "",
      "Flagged for review:",
      "",
      ...flagged.map((line) => `- ${line}`),
    );
  const revisions = [
    ...new Set(
      arms.flatMap((arm) =>
        arm.trials.flatMap((trial) => trial.sourceRevision ?? []),
      ),
    ),
  ];
  const mcpRevisions = [
    ...new Set(
      arms.flatMap((arm) =>
        arm.trials.flatMap((trial) => trial.kernelMcpSha ?? []),
      ),
    ),
  ];
  lines.push(
    "",
    `Task/tool source: ${revisions.join(", ") || "unavailable"}. Kernel MCP source: ${mcpRevisions.join(", ") || "unavailable"}.`,
  );
  return lines.join("\n");
}

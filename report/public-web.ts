import {
  type BenchmarkArm,
  type BenchmarkTrial,
  median,
  summarizeArm,
} from "./results";

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

export function publicWebStats(trials: BenchmarkTrial[]) {
  const costs = values(trials, (trial) => trial.metrics.costUsd);
  return {
    recorded: trials.length,
    passes: trials.filter(passed).length,
    pageVerified: trials.filter(
      (trial) => passed(trial) && trial.rewards.page_verified === 1,
    ).length,
    finalized: trials.filter((trial) => trial.rewards.finalized === 1).length,
    adopted: trials.filter((trial) => trial.rewards.webmcp_invoked === 1)
      .length,
    medianAgentMs: median(
      values(trials, (trial) => trial.phases.agentExecution?.durationMs),
    ),
    medianPassedAgentMs: median(
      values(
        trials.filter(passed),
        (trial) => trial.phases.agentExecution?.durationMs,
      ),
    ),
    medianCalls: median(values(trials, (trial) => trial.metrics.toolCalls)),
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

export function summarizePublicWeb(arms: BenchmarkArm[]) {
  return arms.map((arm) => ({
    arm: arm.name,
    configuration: summarizeArm(arm).configuration,
    intended: arm.nTotalTrials,
    ...publicWebStats(arm.trials),
    tasks: [...new Set(arm.trials.map((trial) => trial.taskName))]
      .sort()
      .map((task) => ({
        task,
        grading: [
          ...new Set(
            arm.trials
              .filter((trial) => trial.taskName === task)
              .flatMap((trial) => trial.taskGrading ?? []),
          ),
        ],
        ...publicWebStats(
          arm.trials.filter((trial) => trial.taskName === task),
        ),
      })),
  }));
}

const count = (value?: number) =>
  value === undefined ? "—" : String(Math.round(value));
const seconds = (value?: number) =>
  value === undefined ? "—" : `${Math.round(value / 1000)}s`;
const table = (headers: string[], rows: (string | number)[][]) =>
  [
    `| ${headers.join(" | ")} |`,
    `|${headers.map(() => "---").join("|")}|`,
    ...rows.map((row) => `| ${row.join(" | ")} |`),
  ].join("\n");

export function renderPublicWeb(arms: BenchmarkArm[]): string {
  const summaries = summarizePublicWeb(arms);
  const lines = [
    table(
      [
        "Arm",
        "Agent / model",
        "Pass / intended",
        "Page-checked passes",
        "Finalized",
        "WebMCP used",
        "Median agent time",
        "Median agent time (passes)",
        "Median calls",
        "Median input / cached / output tokens",
        "Recorded model cost (priced / recorded)",
      ],
      summaries.map((s) => [
        s.arm,
        s.configuration ?? "unknown",
        `${s.passes}/${s.intended}`,
        s.pageVerified,
        s.finalized,
        s.adopted,
        seconds(s.medianAgentMs),
        seconds(s.medianPassedAgentMs),
        count(s.medianCalls),
        `${count(s.medianInputTokens)} / ${count(s.medianCacheTokens)} / ${count(s.medianOutputTokens)}`,
        `${s.recordedCostUsd === undefined ? "—" : `$${s.recordedCostUsd.toFixed(4)}`} (${s.pricedTrials}/${s.recorded})`,
      ]),
    ),
    "",
    "### Per task",
    "",
    table(
      [
        "Task",
        "Arm",
        "Grading",
        "Pass / recorded",
        "Median agent time",
        "Median agent time (passes)",
        "Median input / output tokens",
      ],
      summaries.flatMap((s) =>
        s.tasks.map((t) => [
          t.task,
          s.arm,
          t.grading.join(", ") || "unknown",
          `${t.passes}/${t.recorded}`,
          seconds(t.medianAgentMs),
          seconds(t.medianPassedAgentMs),
          `${count(t.medianInputTokens)} / ${count(t.medianOutputTokens)}`,
        ]),
      ),
    ),
    "",
    "Pass means the task's answer rules (and page end-state check, when present) passed and the agent used its assigned browser toolset. Shape/range and pinned-answer rules do not independently verify every returned fact. Page-checked passes are reported separately.",
    "",
    "Agent time excludes browser/container setup and verification. Arms run concurrently in the same window; repetitions are not matched pairs, so these tables make no paired speedup or significance claim. Per-task denominators count recorded trials; the arm totals retain all intended trials, including missing and infrastructure-failed trials. Cost covers only priced trials, not browser costs or unreported model charges.",
  ];
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

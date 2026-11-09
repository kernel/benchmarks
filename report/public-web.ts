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

// Strongest check first. A shape/range pass shows only a well-formed, in-range answer.
const GRADINGS = ["end state", "pinned rules", "shape/range rules"];
const grading = (trial: BenchmarkTrial) => trial.taskGrading ?? "not graded";
const gradingRank = (name: string) =>
  GRADINGS.includes(name) ? GRADINGS.indexOf(name) : GRADINGS.length;

function passesByGrading(trials: BenchmarkTrial[]) {
  return [...new Set(trials.map(grading))]
    .sort((a, b) => gradingRank(a) - gradingRank(b))
    .map((name) => {
      const graded = trials.filter((trial) => grading(trial) === name);
      return {
        grading: name,
        passes: graded.filter(passed).length,
        recorded: graded.length,
      };
    });
}

export function publicWebStats(trials: BenchmarkTrial[]) {
  const costs = values(trials, (trial) => trial.metrics.costUsd);
  return {
    recorded: trials.length,
    passes: trials.filter(passed).length,
    passesByGrading: passesByGrading(trials),
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
        "Passes by grading",
        "Finalized",
        "Toolset valid",
        "Rejected-call trials",
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
        s.passesByGrading
          .map((g) => `${g.grading} ${g.passes}/${g.recorded}`)
          .join(" · ") || "—",
        s.finalized,
        `${s.toolsetValid}/${s.toolsetChecked}`,
        s.rejectedCallTrials,
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
      summaries
        .flatMap((s) => s.tasks.map((t) => ({ arm: s.arm, ...t })))
        .sort((a, b) => a.task.localeCompare(b.task))
        .map((t) => [
          t.task,
          t.arm,
          t.grading.join(", ") || "unknown",
          `${t.passes}/${t.recorded}`,
          seconds(t.medianAgentMs),
          seconds(t.medianPassedAgentMs),
          `${count(t.medianInputTokens)} / ${count(t.medianOutputTokens)}`,
        ]),
    ),
    "",
    "Pass means the task's answer rules (and page end-state check, when present) passed and the agent used its assigned browser toolset. Passes by grading splits them by how the task is checked, over recorded trials: a shape/range pass shows only a well-formed, in-range answer, and pinned-answer rules do not independently verify every returned fact. A rejected-call trial made a call its arm does not permit; the proxy blocked the call, which does not fail the trial.",
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

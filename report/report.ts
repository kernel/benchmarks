#!/usr/bin/env bun
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import {
  type ArmSummary,
  compareTasks,
  medianSpeedup,
  parseArmSpec,
  readBenchmarkArm,
  summarizeArm,
  type TaskComparison,
} from "./results";

interface Options {
  arms: string[];
  statuses: Record<string, number>;
  json?: string;
  markdown?: string;
  publication?: string;
  title: string;
}

function parseArgs(args: string[]): Options {
  const options: Options = {
    arms: [],
    statuses: {},
    title: process.env.BENCHMARK_NAME ?? "Benchmark",
  };
  for (let index = 0; index < args.length; index += 1) {
    const flag = args[index];
    const value = args[index + 1];
    if (!value || !flag.startsWith("--"))
      throw new Error(`Missing value for ${flag}`);
    index += 1;
    switch (flag) {
      case "--arm":
        options.arms.push(value);
        break;
      case "--status": {
        const separator = value.indexOf("=");
        const status = Number(value.slice(separator + 1));
        if (
          separator <= 0 ||
          separator === value.length - 1 ||
          !Number.isInteger(status)
        ) {
          throw new Error(
            `Invalid --status ${JSON.stringify(value)}; expected name=exit-code`,
          );
        }
        options.statuses[value.slice(0, separator)] = status;
        break;
      }
      case "--json":
        options.json = value;
        break;
      case "--markdown":
        options.markdown = value;
        break;
      case "--publication":
        options.publication = value;
        break;
      case "--title":
        options.title = value;
        break;
      default:
        throw new Error(`Unknown argument ${flag}`);
    }
  }
  if (options.arms.length === 0)
    throw new Error("At least one --arm name=/job/path is required");
  return options;
}

function ratio(value: number | undefined, denominator: number): string {
  return value === undefined ? "—" : `${value}/${denominator}`;
}

function duration(value: number | undefined): string {
  return value === undefined ? "—" : `${Math.round(value / 1000)}s`;
}

function cost(value: number | undefined): string {
  return value === undefined ? "—" : `$${value.toFixed(4)}`;
}

function renderRewardTables(
  summaries: ArmSummary[],
  comparisons: TaskComparison[],
): string[] {
  const lines = [
    "| Arm | Configuration | Success | Finalized | Infra | Retries | Kernel MCP valid | WebMCP invoked | xwebmcp registered | Median agent time | Median agent time, successes | Median calls | Cost |",
    "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
  ];
  for (const summary of summaries) {
    lines.push(
      `| ${summary.arm} | ${summary.configuration ?? "—"} | ${ratio(summary.lenient, summary.trials)} | ${ratio(summary.finalized, summary.trials)} | ${summary.infraErrors} | ${summary.retries} | ${ratio(summary.kernelMcpValid, summary.kernelMcpChecked)} | ${ratio(summary.webmcpInvoked, summary.webmcpChecked)} | ${ratio(summary.xwebmcpRegistered, summary.xwebmcpChecked)} | ${duration(summary.medianAgentMs)} | ${duration(summary.medianSuccessAgentMs)} | ${summary.medianCalls ?? "—"} | ${cost(summary.totalCostUsd)} |`,
    );
  }
  if (summaries.length < 2 || comparisons.length === 0) return lines;

  const arms = summaries.map((summary) => summary.arm);
  lines.push(
    "",
    `| Task | ${arms.map((arm) => `${arm} success | ${arm} median agent time`).join(" | ")} |`,
    `|---|${arms.map(() => "---:|---:").join("|")}|`,
  );
  for (const comparison of comparisons) {
    const cells = arms.map((arm) => {
      const result = comparison.arms[arm];
      return result
        ? `${result.successes}/${result.trials} | ${duration(result.medianAgentMs)}`
        : "— | —";
    });
    lines.push(`| ${comparison.task} | ${cells.join(" | ")} |`);
  }
  const [baseline, ...candidates] = arms;
  for (const candidate of candidates) {
    const { speedup, tasks } = medianSpeedup(comparisons, baseline, candidate);
    if (speedup !== undefined) {
      lines.push(
        "",
        `${candidate} vs ${baseline}: median speedup ${speedup.toFixed(2)}× in agent time, over the ${tasks} ${tasks === 1 ? "task" : "tasks"} both arms succeeded on.`,
      );
    }
  }
  return lines;
}

export function renderMarkdown(
  title: string,
  summaries: ArmSummary[],
  publication?: Record<string, unknown>,
  statuses: Record<string, number> = {},
  comparisons: TaskComparison[] = [],
): string {
  const lines = [`## ${title}`];
  const failed = Object.entries(statuses).filter(([, status]) => status !== 0);
  const incomplete =
    failed.length > 0 || summaries.some((summary) => !summary.complete);
  if (incomplete) {
    const reasons = [
      ...failed.map(([arm, status]) => `${arm} exited ${status}`),
      ...summaries.flatMap((summary) =>
        summary.incompleteReasons.map((reason) => `${summary.arm} ${reason}`),
      ),
    ];
    lines.push(
      "",
      `> [!WARNING]\n> Incomplete benchmark: ${reasons.join(", ")}. Scores below include only completed Harbor results.`,
    );
  }
  const clawbench = summaries.some(
    (summary) => summary.scoring === "clawbench",
  );
  lines.push("");
  if (clawbench) {
    lines.push(
      "| Arm | Configuration | Lenient | Strict | Intercepted | Infra | Retries | Ungraded | Kernel MCP valid | Median calls | Median duration | Cost |",
      "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    );
    for (const summary of summaries) {
      lines.push(
        `| ${summary.arm} | ${summary.configuration ?? "—"} | ${ratio(summary.lenient, summary.trials)} | ${ratio(summary.strict, summary.trials)} | ${ratio(summary.intercepted, summary.trials)} | ${summary.infraErrors} | ${summary.retries} | ${summary.ungraded} | ${ratio(summary.kernelMcpValid, summary.kernelMcpChecked)} | ${summary.medianCalls ?? "—"} | ${duration(summary.medianDurationMs)} | ${cost(summary.totalCostUsd)} |`,
      );
    }
  } else {
    lines.push(...renderRewardTables(summaries, comparisons));
  }

  if (typeof publication?.url === "string") {
    lines.push("", `[Open the Braintrust experiment](${publication.url})`);
  }
  if (
    process.env.GITHUB_SERVER_URL &&
    process.env.GITHUB_REPOSITORY &&
    process.env.GITHUB_RUN_ID
  ) {
    lines.push(
      "",
      `[Open the GitHub Actions run](${process.env.GITHUB_SERVER_URL}/${process.env.GITHUB_REPOSITORY}/actions/runs/${process.env.GITHUB_RUN_ID})`,
    );
  }
  lines.push(
    "",
    clawbench
      ? "Lenient reward is the primary ClawBench score. Infrastructure failures remain in the intended-task denominator."
      : "Success is the agent's own report through `finalize`. Agent time is the agent's execution, without environment and agent setup. Infrastructure failures remain in the intended-task denominator.",
    "",
  );
  return lines.join("\n");
}

function write(path: string | undefined, content: string): void {
  if (!path) return;
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, content);
}

function main(): void {
  const options = parseArgs(process.argv.slice(2));
  const arms = options.arms.map(parseArmSpec).map(readBenchmarkArm);
  const summaries = arms.map(summarizeArm);
  const comparisons = compareTasks(arms);
  const publication = options.publication
    ? (JSON.parse(readFileSync(options.publication, "utf8")) as Record<
        string,
        unknown
      >)
    : undefined;
  const result = {
    benchmark: process.env.BENCHMARK_NAME,
    generatedAt: new Date().toISOString(),
    arms: summaries,
    tasks: comparisons,
    statuses: options.statuses,
    publication,
  };
  const rendered = renderMarkdown(
    options.title,
    summaries,
    publication,
    options.statuses,
    comparisons,
  );
  write(options.json, `${JSON.stringify(result, undefined, 2)}\n`);
  write(options.markdown, rendered);
  process.stdout.write(rendered);
}

if (import.meta.main) {
  try {
    main();
  } catch (error) {
    console.error(error instanceof Error ? error.message : error);
    process.exitCode = 1;
  }
}

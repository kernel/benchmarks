import { afterEach, expect, test } from "bun:test";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { readBenchmarkArm, summarizeArm } from "./results";
import { publicWebStats, summarizePublicWeb } from "./public-web";
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
    medianAgentMs: 30000,
    medianPassedAgentMs: 20000,
    medianInputTokens: 1000,
    medianCacheTokens: 200,
    medianOutputTokens: 100,
    recordedCostUsd: 0.25,
    pricedTrials: 1,
  });
  expect(
    arm.trials.find((trial) => trial.id === "trial-1")?.metrics.toolCalls,
  ).toBe(7);
  expect(
    arm.trials.find((trial) => trial.id === "trial-3")?.metrics.toolCalls,
  ).toBe(0);
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
  expect(report).toContain("1/4");
  expect(report).toContain("| end state 1/3 | 3 | 2/3 | 1 |");
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
  const mixed = arms[0].trials.map((trial) =>
    trial.id === "trial-1"
      ? trial
      : { ...trial, taskGrading: "shape/range rules" },
  );
  expect(publicWebStats(mixed).passesByGrading).toEqual([
    { grading: "end state", passes: 1, recorded: 1 },
    { grading: "shape/range rules", passes: 0, recorded: 2 },
  ]);
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

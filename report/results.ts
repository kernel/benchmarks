import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { basename, join, resolve } from "node:path";

export type JsonObject = Record<string, unknown>;

export interface ArmInput {
  name: string;
  path: string;
}

export interface BenchmarkMetrics {
  inputTokens?: number;
  cacheTokens?: number;
  outputTokens?: number;
  costUsd?: number;
  durationMs?: number;
  toolCalls?: number;
  start?: number;
  end?: number;
}

export interface BenchmarkPhase {
  startedAt?: string;
  finishedAt?: string;
  start?: number;
  end?: number;
  durationMs?: number;
}

export interface BenchmarkTrial {
  arm: string;
  id: string;
  taskName: string;
  trialName: string;
  source: string;
  agent: string;
  agentVersion?: string;
  agentConfigHash: string;
  model?: string;
  rewards: Record<string, number>;
  scores: Record<string, number>;
  error?: string;
  errorClass?: "infra";
  metrics: BenchmarkMetrics;
  phases: {
    environmentSetup?: BenchmarkPhase;
    agentSetup?: BenchmarkPhase;
    agentExecution?: BenchmarkPhase;
    verifier?: BenchmarkPhase;
  };
  expectedBrowserSessionId?: string;
  kernelMcpSha?: string;
  trajectoryPath?: string;
  startedAt?: string;
  finishedAt?: string;
}

export interface BenchmarkArm {
  name: string;
  path: string;
  jobId: string;
  jobName: string;
  startedAt?: string;
  finishedAt?: string;
  nTotalTrials: number;
  nCompletedTrials: number;
  nErroredTrials: number;
  nCancelledTrials: number;
  nRetries: number;
  trials: BenchmarkTrial[];
}

export interface ArmSummary {
  arm: string;
  trials: number;
  scored: number;
  intercepted: number;
  lenient: number;
  strict?: number;
  strictScored: number;
  infraErrors: number;
  retries: number;
  ungraded: number;
  complete: boolean;
  incompleteReasons: string[];
  kernelMcpValid?: number;
  kernelMcpChecked: number;
  /** "clawbench" when trials report reward_lenient, else "reward" (one binary reward). */
  scoring: "clawbench" | "reward";
  finalized?: number;
  /** Self-reported outcomes other than success, from outcome_<name> rewards. */
  failed?: number;
  websiteErrors?: number;
  kernelErrors?: number;
  webmcpInvoked?: number;
  webmcpChecked: number;
  xwebmcpRegistered?: number;
  xwebmcpChecked: number;
  medianCalls?: number;
  medianDurationMs?: number;
  medianAgentMs?: number;
  medianSuccessAgentMs?: number;
  totalCostUsd?: number;
  configuration?: string;
}

export interface TaskArmResult {
  trials: number;
  successes: number;
  medianCalls?: number;
  medianAgentMs?: number;
  medianSuccessAgentMs?: number;
}

export interface TaskComparison {
  task: string;
  arms: Record<string, TaskArmResult>;
}

function object(value: unknown): JsonObject {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonObject)
    : {};
}

function array(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function string(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function number(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : undefined;
}

function readJson(path: string): JsonObject {
  return object(JSON.parse(readFileSync(path, "utf8")));
}

function readJsonIfPresent(path: string): JsonObject {
  return existsSync(path) ? readJson(path) : {};
}

function errorText(value: unknown): string | undefined {
  if (value === null || value === undefined) return undefined;
  const text =
    typeof value === "string" ? value : JSON.stringify(value, undefined, 2);
  return text.replace(/\s+/g, " ").trim() || undefined;
}

function numericRecord(value: unknown): Record<string, number> {
  return Object.fromEntries(
    Object.entries(object(value)).flatMap(([key, raw]) => {
      const parsed = number(raw);
      return parsed === undefined ? [] : [[key, parsed]];
    }),
  );
}

export function selectPrimaryReward(
  rewards: Record<string, number>,
): { key: "reward_lenient" | "reward"; value: number } | undefined {
  if (rewards.reward_lenient !== undefined) {
    return { key: "reward_lenient", value: rewards.reward_lenient };
  }
  if (rewards.reward !== undefined) {
    return { key: "reward", value: rewards.reward };
  }
  return undefined;
}

function clampScore(value: number): number {
  return Math.min(1, Math.max(0, value));
}

function isoSeconds(value: unknown): number | undefined {
  const timestamp = string(value);
  if (!timestamp) return undefined;
  const millis = Date.parse(timestamp);
  return Number.isFinite(millis) ? millis / 1000 : undefined;
}

function durationMs(
  startedAt?: string,
  finishedAt?: string,
): number | undefined {
  if (!startedAt || !finishedAt) return undefined;
  const duration = Date.parse(finishedAt) - Date.parse(startedAt);
  return Number.isFinite(duration) && duration >= 0 ? duration : undefined;
}

function phase(value: unknown): BenchmarkPhase | undefined {
  const timing = object(value);
  const startedAt = string(timing.started_at);
  const finishedAt = string(timing.finished_at);
  if (!startedAt && !finishedAt) return undefined;
  return {
    startedAt,
    finishedAt,
    start: isoSeconds(startedAt),
    end: isoSeconds(finishedAt),
    durationMs: durationMs(startedAt, finishedAt),
  };
}

function sumStepMetric(
  stepResults: unknown[],
  key: string,
): number | undefined {
  const values = stepResults
    .map((step) => number(object(object(step).agent_result)[key]))
    .filter((value): value is number => value !== undefined);
  return values.length > 0
    ? values.reduce((total, value) => total + value, 0)
    : undefined;
}

function trajectoryMetrics(path: string): Pick<BenchmarkMetrics, "toolCalls"> {
  if (!existsSync(path)) return {};
  const trajectory = readJson(path);
  const toolCalls = array(trajectory.steps)
    .map((step) => array(object(step).tool_calls).length)
    .reduce((total, count) => total + count, 0);
  return { toolCalls };
}

function trialRewards(
  result: JsonObject,
  trialDir: string,
): Record<string, number> {
  const direct = numericRecord(object(result.verifier_result).rewards);
  if (Object.keys(direct).length > 0) return direct;

  const steps = array(result.step_results);
  const lastStep = object(steps.at(-1));
  const fromStep = numericRecord(object(lastStep.verifier_result).rewards);
  if (Object.keys(fromStep).length > 0) return fromStep;

  return numericRecord(
    readJsonIfPresent(join(trialDir, "steps/run/verifier/reward.json")),
  );
}

function trialScores(
  rewards: Record<string, number>,
  hasInfraError: boolean,
): Record<string, number> {
  if (hasInfraError) {
    return { infra_error_rate: 1, ungraded_rate: 1 };
  }

  const primary = selectPrimaryReward(rewards);
  if (!primary) return { infra_error_rate: 0, ungraded_rate: 1 };

  const reward = clampScore(primary.value);
  const scores: Record<string, number> = {
    accuracy: reward,
    false_positive_rate: 0,
    false_negative_rate: 1 - reward,
    infra_error_rate: 0,
    ungraded_rate: 0,
  };
  for (const [key, value] of Object.entries(rewards)) {
    if (value >= 0 && value <= 1) scores[key] = value;
  }
  return scores;
}

function parseTrial(arm: string, trialDir: string): BenchmarkTrial {
  const result = readJson(join(trialDir, "result.json"));
  const config = object(result.config);
  const agentConfig = object(config.agent);
  const agentInfo = object(result.agent_info);
  const modelInfo = object(agentInfo.model_info);
  const steps = array(result.step_results);
  const rewards = trialRewards(result, trialDir);
  const exceptionFile = join(trialDir, "exception.txt");
  const stepException =
    Object.keys(rewards).length === 0
      ? steps.map((step) => object(step).exception_info).find(Boolean)
      : undefined;
  const error = errorText(
    result.exception_info ??
      stepException ??
      (existsSync(exceptionFile)
        ? readFileSync(exceptionFile, "utf8")
        : undefined),
  );
  const startedAt = string(result.started_at);
  const finishedAt = string(result.finished_at);
  const trajectoryPath = join(trialDir, "steps/run/agent/trajectory.json");
  const kernelMcpResult = readJsonIfPresent(
    join(trialDir, "steps/run/verifier/kernel-mcp-result.json"),
  );

  return {
    arm,
    id: string(result.id) ?? basename(trialDir),
    taskName:
      string(result.task_name)?.replace(/^[^/]+\//, "") ??
      string(object(config.task).name) ??
      basename(trialDir).split("__", 1)[0],
    trialName: string(result.trial_name) ?? basename(trialDir),
    source:
      string(result.source) ??
      string(object(config.task).source) ??
      "clawbench",
    agent: string(agentInfo.name) ?? string(agentConfig.name) ?? "unknown",
    agentVersion:
      string(agentInfo.version) ?? string(object(agentConfig.kwargs).version),
    agentConfigHash: createHash("sha256")
      .update(JSON.stringify(agentConfig))
      .digest("hex")
      .slice(0, 8),
    model: string(modelInfo.name) ?? string(agentConfig.model_name),
    rewards,
    scores: trialScores(rewards, error !== undefined),
    error,
    errorClass: error === undefined ? undefined : "infra",
    metrics: {
      inputTokens: sumStepMetric(steps, "n_input_tokens"),
      cacheTokens: sumStepMetric(steps, "n_cache_tokens"),
      outputTokens: sumStepMetric(steps, "n_output_tokens"),
      costUsd: sumStepMetric(steps, "cost_usd"),
      durationMs: durationMs(startedAt, finishedAt),
      start: isoSeconds(startedAt),
      end: isoSeconds(finishedAt),
      ...trajectoryMetrics(trajectoryPath),
    },
    phases: {
      environmentSetup: phase(result.environment_setup),
      agentSetup: phase(result.agent_setup),
      agentExecution: phase(object(steps.at(-1)).agent_execution),
      verifier: phase(object(steps.at(-1)).verifier),
    },
    expectedBrowserSessionId: string(kernelMcpResult.expected_session_id),
    kernelMcpSha: string(kernelMcpResult.kernel_mcp_server_sha),
    trajectoryPath: existsSync(trajectoryPath) ? trajectoryPath : undefined,
    startedAt,
    finishedAt,
  };
}

export function parseArmSpec(spec: string): ArmInput {
  const separator = spec.indexOf("=");
  if (separator <= 0 || separator === spec.length - 1) {
    throw new Error(
      `Invalid --arm ${JSON.stringify(spec)}; expected name=/job/path`,
    );
  }
  return {
    name: spec.slice(0, separator),
    path: resolve(spec.slice(separator + 1)),
  };
}

export function readBenchmarkArm(input: ArmInput): BenchmarkArm {
  const configPath = join(input.path, "config.json");
  const resultPath = join(input.path, "result.json");
  if (!existsSync(configPath) || !existsSync(resultPath)) {
    throw new Error(`${input.path} is not a completed Harbor job directory`);
  }

  const config = readJson(configPath);
  const result = readJson(resultPath);
  const stats = object(result.stats);
  const trials = readdirSync(input.path, { withFileTypes: true })
    .filter(
      (entry) =>
        entry.isDirectory() &&
        existsSync(join(input.path, entry.name, "result.json")),
    )
    .map((entry) => parseTrial(input.name, join(input.path, entry.name)))
    .sort((left, right) => left.taskName.localeCompare(right.taskName));

  return {
    name: input.name,
    path: input.path,
    jobId:
      string(result.id) ??
      createHash("sha256").update(input.path).digest("hex").slice(0, 16),
    jobName: string(config.job_name) ?? basename(input.path),
    startedAt: string(result.started_at),
    finishedAt: string(result.finished_at),
    nTotalTrials: number(result.n_total_trials) ?? trials.length,
    nCompletedTrials: number(stats.n_completed_trials) ?? trials.length,
    nErroredTrials:
      number(stats.n_errored_trials) ??
      trials.filter((trial) => trial.error).length,
    nCancelledTrials: number(stats.n_cancelled_trials) ?? 0,
    nRetries: number(stats.n_retries) ?? 0,
    trials,
  };
}

function median(values: number[]): number | undefined {
  if (values.length === 0) return undefined;
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0
    ? (sorted[middle - 1] + sorted[middle]) / 2
    : sorted[middle];
}

function sum(trials: BenchmarkTrial[], key: string): number | undefined {
  return trials.length === 0
    ? undefined
    : trials.reduce((total, trial) => total + trial.rewards[key], 0);
}

function agentMs(trials: BenchmarkTrial[]): number[] {
  return trials.flatMap((trial) => {
    const duration = trial.phases.agentExecution?.durationMs;
    return duration === undefined ? [] : [duration];
  });
}

function succeeded(trial: BenchmarkTrial): boolean {
  return (selectPrimaryReward(trial.rewards)?.value ?? 0) >= 1;
}

export function summarizeArm(arm: BenchmarkArm): ArmSummary {
  const numeric = (key: string) =>
    arm.trials.filter(
      (trial) =>
        trial.errorClass !== "infra" && trial.rewards[key] !== undefined,
    );
  const graded = arm.trials.filter((trial) => trial.errorClass !== "infra");
  const primary = arm.trials.flatMap((trial) => {
    if (trial.errorClass === "infra") return [];
    const reward = selectPrimaryReward(trial.rewards);
    return reward ? [{ trial, reward }] : [];
  });
  const strict = numeric("reward_strict");
  const validity = numeric("kernel_mcp_valid");
  const webmcp = numeric("webmcp_invoked");
  const registered = numeric("xwebmcp_registered");
  const infraErrors = arm.trials.filter(
    (trial) => trial.errorClass === "infra",
  ).length;
  const ungraded = arm.trials.filter(
    (trial) => trial.errorClass !== "infra" && trial.scores.ungraded_rate === 1,
  ).length;
  const incompleteReasons = [
    ...(arm.nTotalTrials === 0 ? ["had no intended trials"] : []),
    ...(primary.length !== arm.nTotalTrials
      ? [`scored ${primary.length}/${arm.nTotalTrials} trials`]
      : []),
    ...(infraErrors > 0
      ? [
          `had ${infraErrors} infrastructure ${infraErrors === 1 ? "failure" : "failures"}`,
        ]
      : []),
    ...(ungraded > 0
      ? [`had ${ungraded} ungraded ${ungraded === 1 ? "trial" : "trials"}`]
      : []),
  ];
  const costs = arm.trials.flatMap((trial) =>
    trial.metrics.costUsd === undefined ? [] : [trial.metrics.costUsd],
  );
  const configurations = [
    ...new Set(
      arm.trials.map(
        (trial) =>
          `${trial.agent}${trial.agentVersion ? `@${trial.agentVersion}` : ""}${trial.model ? ` · ${trial.model}` : ""} · config ${trial.agentConfigHash}`,
      ),
    ),
  ];

  return {
    arm: arm.name,
    trials: arm.nTotalTrials,
    scored: primary.length,
    intercepted: numeric("intercepted").reduce(
      (total, trial) => total + trial.rewards.intercepted,
      0,
    ),
    lenient: primary.reduce((total, entry) => total + entry.reward.value, 0),
    strict:
      strict.length === 0
        ? undefined
        : strict.reduce(
            (total, trial) => total + trial.rewards.reward_strict,
            0,
          ),
    strictScored: strict.length,
    infraErrors,
    retries: arm.nRetries,
    ungraded,
    complete: incompleteReasons.length === 0,
    incompleteReasons,
    kernelMcpValid:
      validity.length === 0
        ? undefined
        : validity.reduce(
            (total, trial) => total + trial.rewards.kernel_mcp_valid,
            0,
          ),
    kernelMcpChecked: validity.length,
    scoring: arm.trials.some(
      (trial) => trial.rewards.reward_lenient !== undefined,
    )
      ? "clawbench"
      : "reward",
    finalized: sum(numeric("finalized"), "finalized"),
    failed: sum(numeric("outcome_failed"), "outcome_failed"),
    websiteErrors: sum(
      numeric("outcome_website_error"),
      "outcome_website_error",
    ),
    kernelErrors: sum(numeric("outcome_kernel_error"), "outcome_kernel_error"),
    webmcpInvoked: sum(webmcp, "webmcp_invoked"),
    webmcpChecked: webmcp.length,
    xwebmcpRegistered: sum(registered, "xwebmcp_registered"),
    xwebmcpChecked: registered.length,
    medianAgentMs: median(agentMs(graded)),
    medianSuccessAgentMs: median(agentMs(graded.filter(succeeded))),
    medianCalls: median(
      arm.trials.flatMap((trial) =>
        trial.metrics.toolCalls === undefined ? [] : [trial.metrics.toolCalls],
      ),
    ),
    medianDurationMs: median(
      arm.trials.flatMap((trial) =>
        trial.metrics.durationMs === undefined
          ? []
          : [trial.metrics.durationMs],
      ),
    ),
    totalCostUsd:
      costs.length === 0
        ? undefined
        : costs.reduce((total, cost) => total + cost, 0),
    configuration:
      configurations.length === 0
        ? undefined
        : configurations.length === 1
          ? configurations[0]
          : `mixed: ${configurations.join(", ")}`,
  };
}

/** Each task's successes and agent time per arm, over trials without infrastructure errors. */
export function compareTasks(arms: BenchmarkArm[]): TaskComparison[] {
  const tasks = [
    ...new Set(
      arms.flatMap((arm) => arm.trials.map((trial) => trial.taskName)),
    ),
  ].sort();
  return tasks.map((task) => ({
    task,
    arms: Object.fromEntries(
      arms.map((arm) => {
        const trials = arm.trials.filter(
          (trial) => trial.taskName === task && trial.errorClass !== "infra",
        );
        return [
          arm.name,
          {
            trials: trials.length,
            successes: trials.filter(succeeded).length,
            medianCalls: median(
              trials.flatMap((trial) =>
                trial.metrics.toolCalls === undefined
                  ? []
                  : [trial.metrics.toolCalls],
              ),
            ),
            medianAgentMs: median(agentMs(trials)),
            medianSuccessAgentMs: median(agentMs(trials.filter(succeeded))),
          },
        ];
      }),
    ),
  }));
}

/**
 * The median, over tasks both arms succeeded on at least once, of the
 * baseline's median successful agent time divided by the candidate's.
 * Above 1 means the candidate finished faster.
 */
export function medianSpeedup(
  comparisons: TaskComparison[],
  baseline: string,
  candidate: string,
): { speedup?: number; tasks: number } {
  const ratios = comparisons.flatMap((comparison) => {
    const before = comparison.arms[baseline]?.medianSuccessAgentMs;
    const after = comparison.arms[candidate]?.medianSuccessAgentMs;
    return before !== undefined && after !== undefined && after > 0
      ? [before / after]
      : [];
  });
  return { speedup: median(ratios), tasks: ratios.length };
}

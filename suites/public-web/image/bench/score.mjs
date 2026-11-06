#!/usr/bin/env node
import { existsSync, readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import { execute } from "./api.mjs";

export async function score(task, report, readEndState, gradeAnswer) {
  let endState = null;
  let pageError = null;
  if (task.intent.check && report?.outcome === "success") {
    try { endState = { output: await readEndState(task.intent.check) }; }
    catch (error) { pageError = error.message; }
  }
  const grade = gradeAnswer(task.intent, task.schema, report?.evidence, endState);
  const passed = report?.outcome === "success" && grade.outcome === "correct" && !pageError;
  return {
    grade, endState, pageError,
    rewards: {
      reward: Number(passed), finalized: Number(Boolean(report)),
      rule_pass: Number(grade.outcome === "correct"),
      page_verified: Number(passed && grade.end_state === true),
      page_unverifiable: Number(Boolean(pageError)),
      ...Object.fromEntries(["failed", "blocked", "website_error", "kernel_error"].map(name => [`outcome_${name}`, Number(report?.outcome === name)])),
    },
  };
}

async function main() {
  const task = JSON.parse(readFileSync(new URL("./task.json", import.meta.url), "utf8"));
  const { gradeAnswer } = await import("./upstream/bench/grade.mjs");
  const { readPage, readPageArg } = await import("./upstream/bench/lib/page.mjs");
  const readJson = (file) => existsSync(file) ? JSON.parse(readFileSync(file, "utf8")) : null;
  const report = readJson("/logs/agent/finalize.json");
  const result = await score(task, report, check =>
    execute(`return await page.evaluate(${readPage.toString()}, ${JSON.stringify(readPageArg(check.fields))});`), gradeAnswer);
  mkdirSync("/logs/verifier", { recursive: true });
  const outputs = {
    "reward.json": result.rewards, "grade.json": result.grade, "end-state.json": result.endState,
    "finalize.json": report, "setup.json": readJson("/run/public-web/setup.json"),
    "source.json": { sha: task.source_sha, grading: task.grading, task: task.intent.id },
    "page-error.json": result.pageError,
  };
  for (const [file, value] of Object.entries(outputs)) {
    writeFileSync(`/logs/verifier/${file}`, JSON.stringify(value, null, 2) + "\n");
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();

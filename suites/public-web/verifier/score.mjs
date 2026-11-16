#!/usr/bin/env node
import { existsSync, readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";

export function readDepartureDates(root = document) {
  return [...root.querySelectorAll('[data-cy="flight-date"], .summary .flight-date')]
    .map(element => element.innerText.replace(/^Journey Date\s*:?\s*/i, "").trim())
    .filter(Boolean);
}

function matchesDepartureDate(value, expected) {
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value === expected;
  const match = /^(?:([a-z]+),?\s+)?([a-z]+)\s+(\d{1,2})(?:,?\s+(\d{4}))?$/i.exec(value);
  if (!match) return false;
  const [, weekday, month, day, year] = match;
  const date = new Date(`${expected}T00:00:00Z`);
  const nameMatches = (name, field) => ["short", "long"].some(style =>
    new Intl.DateTimeFormat("en-US", { timeZone: "UTC", [field]: style }).format(date).toLowerCase() === name.toLowerCase());
  return nameMatches(month, "month") && Number(day) === date.getUTCDate()
    && (!year || Number(year) === date.getUTCFullYear())
    && (!weekday || nameMatches(weekday, "weekday"));
}

export async function score(task, report, readEndState, gradeAnswer, wait = sleep) {
  let endState = null;
  let pageError = null;
  if (task.intent.check && report?.outcome === "success") {
    // The agent can finish while its last action is still loading a page, so a read that
    // navigation interrupts is tried again on the new page.
    for (let attempt = 1; !endState && !pageError; attempt++) {
      try { endState = { output: await readEndState(task.intent.check) }; }
      catch (error) {
        if (attempt < 3 && /Execution context was destroyed/.test(error.message)) await wait(3000);
        else pageError = error.message;
      }
    }
  }
  const grade = gradeAnswer(task.intent, task.schema, report?.evidence, endState);
  if (task.intent.id === "delta-cart-main-classic" && grade.outcome === "correct") {
    const dates = endState?.output?.departure_dates ?? [];
    grade.end_state_values = { ...grade.end_state_values, departure_dates: dates };
    if (!dates.length || !dates.every(date => matchesDepartureDate(date, task.intent.fixture.date))) {
      grade.outcome = "wrong";
      grade.end_state = false;
      grade.problems.push(`end state: departure date does not match ${task.intent.fixture.date}`);
    }
  }
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
  const { execute } = await import("./api.mjs");
  const { gradeAnswer } = await import("./upstream/bench/grade.mjs");
  const { readPage, readPageArg } = await import("./upstream/bench/lib/page.mjs");
  const readJson = (file) => existsSync(file) ? JSON.parse(readFileSync(file, "utf8")) : null;
  const report = readJson("/logs/agent/finalize.json");
  const result = await score(task, report, check =>
    execute(`return await page.evaluate(arg => {
      const output = (${readPage.toString()})(arg);
      ${task.intent.id === "delta-cart-main-classic" ? `output.departure_dates = (${readDepartureDates.toString()})();` : ""}
      return output;
    }, ${JSON.stringify(readPageArg(check.fields))});`), gradeAnswer);
  mkdirSync("/logs/verifier", { recursive: true });
  const outputs = {
    "reward.json": result.rewards, "grade.json": result.grade, "end-state.json": result.endState,
    "finalize.json": report, "setup.json": readJson("/run/public-web/setup.json"),
    "source.json": {
      sha: task.source_sha, grading: task.grading, task: task.intent.id, domain: task.intent.domain,
      stage: task.intent.stage, agreement: task.intent.agreement ?? null, limits: task.limits ?? null,
    },
    "page-error.json": result.pageError,
  };
  for (const [file, value] of Object.entries(outputs)) {
    writeFileSync(`/logs/verifier/${file}`, JSON.stringify(value, null, 2) + "\n");
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();

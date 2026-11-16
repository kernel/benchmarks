#!/usr/bin/env node
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { setTimeout as sleep } from "node:timers/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { api, execute } from "./api.mjs";

export async function setup(task, approach, toolSource, wait = sleep) {
  if (!["control", "treatment", "playwright"].includes(approach)) throw new Error("Missing browser approach");
  const page = await execute(`
await page.goto(${JSON.stringify(task.intent.start_url)}, { waitUntil: "domcontentloaded", timeout: 45000 });
await page.waitForLoadState("load", { timeout: 15000 }).catch(() => {});
return { url: page.url(), title: await page.title() };
`);
  const loadedAt = performance.now();
  const url = new URL(page.url);
  if (url.protocol !== "https:" || !(url.hostname === task.intent.domain || url.hostname.endsWith(`.${task.intent.domain}`))) {
    throw new Error("The start page redirected outside the task's domain");
  }
  const before = (await api("/webmcp/custom-tools")).tools;
  if (before.length) throw new Error("The fresh browser already has custom tools");
  if (approach === "treatment") {
    // Overwriting the namespace replaces a batch an earlier attempt registered, so retrying is safe.
    const added = await api("/webmcp/custom-tools", {
      namespace: task.intent.domain, source: toolSource, force_overwrite_namespace: true,
    }, { retry: true });
    const names = added.tools.map(tool => tool.tool.name).sort();
    if (JSON.stringify(names) !== JSON.stringify([...task.registered].sort())) throw new Error("The registered tool set differs from the pinned batch");
  }
  const live = (await api("/webmcp/tools")).tools;
  if (approach !== "treatment" && live.some(tool => tool.source?.custom)) throw new Error("A baseline browser has custom tools");
  if (approach === "treatment" && task.intent.entry_tool && !live.some(tool =>
    tool.tool?.name === task.intent.entry_tool && tool.source?.custom?.namespace === task.intent.domain)) {
    throw new Error("The entry tool is not live on the start page");
  }
  await wait(Math.max(0, 10_000 - (performance.now() - loadedAt)));
  return {
    approach, source_sha: task.source_sha, grading: task.grading, start_url: page.url,
    registered: approach === "treatment" ? task.registered : [],
    native_tools: live.filter(tool => !tool.source?.custom).map(tool => tool.tool?.name),
    start_delay_ms: Math.round(performance.now() - loadedAt),
  };
}

async function main() {
  const task = JSON.parse(readFileSync(new URL("./start.json", import.meta.url), "utf8"));
  const approach = process.env.BROWSER_APPROACH;
  const source = approach === "treatment" ? readFileSync(new URL("./tools.js", import.meta.url), "utf8") : null;
  const result = await setup(task, approach, source);
  mkdirSync("/run/public-web", { recursive: true });
  writeFileSync("/run/public-web/setup.json", JSON.stringify(result, null, 2) + "\n");
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();

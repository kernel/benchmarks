import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const [sourcePath, output, selection] = process.argv.slice(2);
const source = resolve(sourcePath);
const revision = readFileSync(join(source, "SOURCE_SHA"), "utf8").trim();
const load = (file) => import(pathToFileURL(join(source, file)).href);
const { loadIntents, loadSchema, resolveFixture, pastDates, fill } = await load("bench/lib/intents.mjs");
const { registeredTools, composeBatch } = await load("bench/lib/tools.mjs");
const { answerCheck } = await load("bench/report.mjs");
const selected = JSON.parse(selection);
const all = loadIntents(join(source, "bench"));
const start = new Date();

for (const id of selected) {
  const original = all.find(intent => intent.id === id);
  if (!original) throw new Error(`Unknown upstream task: ${id}`);
  if (original.profile || original.opt_in || original.allow?.length || original.guard) {
    throw new Error(`${id} requires a profile, opt-in or checkout guard; it is not in the public signed-out suite`);
  }
  const intent = { ...original, fixture: resolveFixture(original.fixture, start),
    tools: [{ repo: "kernel/xwebmcp", ref: revision }] };
  if (pastDates(intent.fixture, start.toISOString().slice(0, 10)).length) throw new Error(`${id} has expired fixtures`);
  const tools = await registeredTools(intent, [revision], (_, file) => readFileSync(join(source, file), "utf8"));
  if (!tools.length) throw new Error(`${id} has no tools at the pinned revision`);
  const dir = join(output, id);
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "task.json"), JSON.stringify({
    intent, schema: loadSchema(join(source, "bench"), intent),
    grading: { "end state": "end state", pinned: "pinned rules", agreement: "shape/range rules", "shape only": "shape/range rules" }[answerCheck(intent)],
    prompt: fill(intent.prompt, intent.fixture), registered: tools.map(tool => tool.name),
  }, null, 2) + "\n");
  writeFileSync(join(dir, "tools.js"), composeBatch(tools));
}

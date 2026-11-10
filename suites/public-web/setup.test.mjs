import assert from "node:assert/strict";
import test from "node:test";
import { setup } from "./image/bench/setup.mjs";

process.env.KERNEL_API_KEY = "stub";
process.env.KERNEL_SESSION_ID = "stub";
process.env.KERNEL_BASE_URL = "https://kernel.invalid";
const task = { intent: { domain: "example.com", start_url: "https://example.com", entry_tool: "search" }, registered: ["search"], source_sha: "pinned", grading: "pinned rules" };
const native = { tool: { name: "native_search" }, source: { page_url: "https://example.com" } };
const custom = { tool: { name: "search" }, source: { custom: { namespace: "example.com" }, page_url: "https://example.com" } };

function stub(context, { url = "https://example.com", before = [], added = [custom], live = [native], failures = 0 } = {}) {
  const calls = [];
  context.mock.method(globalThis, "fetch", async (urlString, init) => {
    const path = new URL(urlString).pathname.replace("/browsers/stub", "");
    const body = init.body && JSON.parse(init.body);
    calls.push({ path, method: init.method, body });
    if (path === "/playwright/execute") return Response.json({ success: true, result: { url, title: "Fixture" } });
    if (path === "/webmcp/custom-tools" && init.method === "POST" && failures-- > 0) return new Response(null, { status: 503 });
    if (path === "/webmcp/custom-tools") return Response.json({ tools: init.method === "POST" ? added : before });
    if (path === "/webmcp/tools") return Response.json({ tools: live });
    assert.fail(`unexpected request ${path}`);
  });
  return calls;
}

for (const approach of ["control", "playwright", "treatment"]) {
  test(`${approach} starts on the task page and registers only the treatment batch`, async context => {
    const calls = stub(context, { live: approach === "treatment" ? [native, custom] : [native] });
    const result = await setup(task, approach, "source", async ms => assert.ok(ms > 9000 && ms <= 10000));
    assert.deepEqual(result.native_tools, ["native_search"]);
    assert.deepEqual(result.registered, approach === "treatment" ? ["search"] : []);
    assert.equal(calls.filter(call => call.path === "/webmcp/custom-tools" && call.method === "POST").length, approach === "treatment" ? 1 : 0);
    if (approach === "treatment") assert.deepEqual(calls[2].body, { namespace: "example.com", source: "source", force_overwrite_namespace: true });
  });
}

test("registration is sent again after a transient failure", async context => {
  const calls = stub(context, { live: [native, custom], failures: 1 });
  const result = await setup(task, "treatment", "source", async () => {});
  assert.deepEqual(result.registered, ["search"]);
  assert.equal(calls.filter(call => call.path === "/webmcp/custom-tools" && call.method === "POST").length, 2);
});

for (const [name, options, approach, message] of [
  ["off-domain redirect", { url: "https://example.com.attacker.invalid" }, "control", /outside the task's domain/],
  ["pre-existing custom tools", { before: [custom] }, "control", /already has custom tools/],
  ["stray live baseline tools", { live: [custom] }, "playwright", /baseline browser has custom tools/],
  ["incomplete registration", { added: [] }, "treatment", /differs from the pinned batch/],
  ["missing entry tool", { live: [native] }, "treatment", /entry tool is not live/],
]) {
  test(`setup refuses ${name}`, async context => {
    stub(context, options);
    await assert.rejects(setup(task, approach, "source", async () => assert.fail("should not wait")), message);
  });
}

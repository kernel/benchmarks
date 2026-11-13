import assert from "node:assert/strict";
import test from "node:test";
import { api, execute } from "./image/bench/api.mjs";

process.env.KERNEL_API_KEY = "stub";
process.env.KERNEL_SESSION_ID = "stub";
process.env.KERNEL_BASE_URL = "https://kernel.invalid";

function stub(context, replies) {
  const calls = [];
  context.mock.method(globalThis, "fetch", async (url, init) => {
    calls.push(init.method);
    const reply = replies.shift();
    if (reply instanceof Error) throw reply;
    return reply;
  });
  return calls;
}

test("a read is retried after a transient failure", async context => {
  const calls = stub(context, [new Response(null, { status: 503 }), Response.json({ tools: [] })]);
  assert.deepEqual(await api("/webmcp/tools"), { tools: [] });
  assert.deepEqual(calls, ["GET", "GET"]);
});

test("page code is retried after a dropped connection", async context => {
  const calls = stub(context, [new TypeError("fetch failed"), Response.json({ success: true, result: "page" })]);
  assert.equal(await execute("return 1"), "page");
  assert.deepEqual(calls, ["POST", "POST"]);
});

test("a request that changes the browser is not retried unless the caller allows it", async context => {
  const calls = stub(context, [new Response(null, { status: 503 }), Response.json({ tools: [] })]);
  await assert.rejects(api("/webmcp/custom-tools", { namespace: "example.com", source: "[]" }), /HTTP 503/);
  assert.deepEqual(calls, ["POST"]);
});

test("a permanent failure is not retried", async context => {
  const calls = stub(context, [new Response(null, { status: 404 }), Response.json({ tools: [] })]);
  await assert.rejects(api("/webmcp/tools"), /HTTP 404/);
  assert.deepEqual(calls, ["GET"]);
});

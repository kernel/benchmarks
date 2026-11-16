import assert from "node:assert/strict";
import test from "node:test";
import { readDepartureDates, score } from "./verifier/score.mjs";

const task = { intent: {}, schema: {} };
const success = { outcome: "success", evidence: '{"answer": 1}' };
const correct = () => ({ outcome: "correct", end_state: null });

test("the agent's success claim alone cannot pass the task", async () => {
  const result = await score(task, success, () => assert.fail("no page check"), () => ({ outcome: "wrong" }));
  assert.equal(result.rewards.reward, 0);
  assert.equal(result.rewards.finalized, 1);
  const failed = await score(task, { ...success, outcome: "blocked" }, null, correct);
  assert.equal(failed.rewards.reward, 0);
  assert.equal(failed.rewards.outcome_blocked, 1);
  const missing = await score(task, null, null, () => ({ outcome: "malformed" }));
  assert.equal(missing.rewards.reward, 0);
  assert.equal(missing.rewards.finalized, 0);
});

test("rule-only passes are distinct from page-verified passes", async () => {
  const result = await score(task, success, null, correct);
  assert.equal(result.rewards.reward, 1);
  assert.equal(result.rewards.page_verified, 0);
  assert.equal(result.rewards.rule_pass, 1);
});

test("page evidence goes to the independent grader and unreadable pages do not pass", async () => {
  const checked = { ...task, intent: { check: { fields: { total: "#total" } } } };
  const page = { url: "https://example.com/cart", text: "Total $42" };
  const result = await score(checked, success, async check => {
    assert.deepEqual(check, checked.intent.check);
    return page;
  }, (intent, schema, evidence, endState) => {
    assert.equal(evidence, success.evidence);
    assert.deepEqual(endState, { output: page });
    return { outcome: "correct", end_state: true };
  });
  assert.equal(result.rewards.page_verified, 1);
  assert.deepEqual(result.endState.output, page);
  const unreadable = await score(checked, success, async () => { throw new Error("browser closed"); }, correct);
  assert.equal(unreadable.rewards.reward, 0);
  assert.equal(unreadable.rewards.page_unverifiable, 1);
  assert.equal(unreadable.pageError, "browser closed");
});

test("departure dates come from the cart itinerary, not unrelated page text", () => {
  const dates = readDepartureDates({ querySelectorAll(selector) {
    assert.equal(selector, '[data-cy="flight-date"], .summary .flight-date');
    return [{ innerText: "Journey Date \nMon, Dec 14" }, { innerText: " Mon, Dec 14 " }, { innerText: "" }];
  } });
  assert.deepEqual(dates, ["Mon, Dec 14", "Mon, Dec 14"]);
});

test("the cart's departure date must match the requested date", async () => {
  const cart = { intent: { id: "delta-cart-main-classic", fixture: { date: "2026-12-14" }, check: {} }, schema: {} };
  const gradeCart = () => ({ outcome: "correct", end_state: true, end_state_values: {}, problems: [] });
  for (const date of ["Mon, Dec 14", "Monday, December 14, 2026", "December 14, 2026", "2026-12-14"]) {
    const result = await score(cart, success, async () => ({ departure_dates: [date] }), gradeCart);
    assert.equal(result.rewards.page_verified, 1, date);
  }
  for (const dates of [[], ["Tue, Dec 15"], ["Mon, Nov 14"], ["Dec 14, 2027"], ["Tue, Dec 14"], ["Mon, Dec 014"], ["not a date"], ["Mon, Dec 14", "Tue, Dec 15"]]) {
    const result = await score(cart, success, async () => ({
      text: "Search date: December 14, 2026", departure_dates: dates,
    }), gradeCart);
    assert.equal(result.rewards.reward, 0, JSON.stringify(dates));
    assert.equal(result.rewards.page_verified, 0);
    assert.equal(result.grade.outcome, "wrong");
    assert.equal(result.grade.end_state, false);
    assert.match(result.grade.problems[0], /departure date.*2026-12-14/);
  }
});

test("departure dates handle zero-padded days and year boundaries", async () => {
  for (const [expected, displayed] of [["2026-10-01", "Thu, Oct 01"], ["2027-01-01", "Friday, January 1, 2027"]]) {
    const cart = { intent: { id: "delta-cart-main-classic", fixture: { date: expected }, check: {} }, schema: {} };
    const gradeCart = () => ({ outcome: "correct", end_state: true, end_state_values: {}, problems: [] });
    const result = await score(cart, success, async () => ({ departure_dates: [displayed] }), gradeCart);
    assert.equal(result.rewards.reward, 1, displayed);
  }
});

test("a page read that navigation interrupts is tried again", async () => {
  const checked = { ...task, intent: { check: {} } };
  const interrupted = new Error("page.evaluate: Execution context was destroyed, most likely because of a navigation");
  const waits = [];
  let reads = 0;
  const settled = await score(checked, success, async () => {
    if (++reads < 3) throw interrupted;
    return { url: "https://example.com/cart" };
  }, () => ({ outcome: "correct", end_state: true }), async ms => waits.push(ms));
  assert.equal(settled.rewards.page_verified, 1);
  assert.deepEqual(waits, [3000, 3000]);
  reads = 0;
  const never = await score(checked, success, async () => { reads++; throw interrupted; }, correct, async () => {});
  assert.equal(reads, 3);
  assert.equal(never.rewards.reward, 0);
  assert.equal(never.rewards.page_unverifiable, 1);
});

import { setTimeout as sleep } from "node:timers/promises";

const TRANSIENT = new Set([408, 429, 500, 502, 503, 504]);

// Reads are tried up to three times on a transient failure. A request that changes the browser
// is not, unless the caller says running it twice is safe: a lost response may hide a change.
export async function api(path, body, { retry = body === undefined } = {}) {
  const base = (process.env.KERNEL_BASE_URL || "https://api.onkernel.com").replace(/\/$/, "");
  for (let attempt = 1; ; attempt++) {
    const last = !retry || attempt === 3;
    let response;
    try {
      response = await fetch(`${base}/browsers/${process.env.KERNEL_SESSION_ID}${path}`, {
        method: body === undefined ? "GET" : "POST",
        headers: { Authorization: `Bearer ${process.env.KERNEL_API_KEY}`, "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(110_000),
      });
    } catch (error) {
      if (last || error.name === "TimeoutError") throw error;
    }
    if (response?.ok) return response.status === 204 ? null : response.json();
    if (response && (last || !TRANSIENT.has(response.status))) throw new Error(`Kernel API returned HTTP ${response.status}`);
    await sleep(300 * 3 ** (attempt - 1));
  }
}

// Callers only navigate to the start page or read the page, so the code may run twice.
export async function execute(code) {
  const response = await api("/playwright/execute", { code, timeout_sec: 90 }, { retry: true });
  if (!response.success) throw new Error(response.error || "Browser execution failed");
  return response.result;
}

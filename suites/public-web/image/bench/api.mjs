export async function api(path, body, method = body === undefined ? "GET" : "POST") {
  const base = (process.env.KERNEL_BASE_URL || "https://api.onkernel.com").replace(/\/$/, "");
  const response = await fetch(`${base}/browsers/${process.env.KERNEL_SESSION_ID}${path}`, {
    method, headers: { Authorization: `Bearer ${process.env.KERNEL_API_KEY}`, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(110_000),
  });
  if (!response.ok) throw new Error(`Kernel API returned HTTP ${response.status}`);
  return response.status === 204 ? null : response.json();
}

export async function execute(code) {
  const response = await api("/playwright/execute", { code, timeout_sec: 90 });
  if (!response.success) throw new Error(response.error || "Browser execution failed");
  return response.result;
}

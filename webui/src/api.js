const locationSearch = typeof window !== "undefined"
  ? window.location.search
  : "";
const sessionToken = new URLSearchParams(locationSearch).get("token") || "";
let currentOrigin = "manual";
let accountGeneration = 0;
let accountHandoffActive = false;
const inFlightControllers = new Set();

export function beginAccountHandoff(action = "switch") {
  accountHandoffActive = true;
  accountGeneration += 1;
  for (const controller of inFlightControllers) {
    controller.abort();
  }
  inFlightControllers.clear();
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("langlang-account-handoff", {
      detail: { action },
    }));
  }
}

export function isAccountHandoffActive() {
  return accountHandoffActive;
}

export async function call(action, payload = {}, options = {}) {
  if (accountHandoffActive) {
    const error = new Error("账户会话已切换，旧请求结果已丢弃");
    error.code = "ACCOUNT_HANDOFF";
    throw error;
  }
  const origin = String(options.origin || currentOrigin || "manual").slice(0, 80);
  const generation = accountGeneration;
  const controller = new AbortController();
  const externalSignal = options.signal;
  const abortFromExternal = () => controller.abort();
  if (externalSignal) {
    if (externalSignal.aborted) controller.abort();
    else externalSignal.addEventListener("abort", abortFromExternal, { once: true });
  }
  inFlightControllers.add(controller);
  try {
    const response = await fetch("/api/action", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-LangLang-Session": sessionToken,
        "X-LangLang-Origin": origin,
        "X-LangLang-Account-Generation": String(generation),
      },
      body: JSON.stringify({ action, payload }),
      keepalive: Boolean(options.keepalive),
      signal: controller.signal,
    });
    const body = await response.json().catch(() => ({}));
    if (generation !== accountGeneration || accountHandoffActive) {
      const error = new Error("账户会话已切换，旧请求结果已丢弃");
      error.code = "ACCOUNT_HANDOFF_STALE";
      throw error;
    }
    if (!response.ok || body.ok === false) {
      const error = new Error(body.error || `操作失败（${response.status}）`);
      error.status = response.status;
      error.code = String(body.code || "");
      throw error;
    }
    return body.data;
  } catch (cause) {
    if (cause?.name === "AbortError") {
      const error = new Error("账户会话已切换，旧请求已取消");
      error.code = "ACCOUNT_HANDOFF_ABORTED";
      throw error;
    }
    throw cause;
  } finally {
    inFlightControllers.delete(controller);
    if (externalSignal) {
      externalSignal.removeEventListener("abort", abortFromExternal);
    }
  }
}

export async function withActionOrigin(origin, operation) {
  const previous = currentOrigin;
  currentOrigin = String(origin || "manual");
  try {
    return await operation();
  } finally {
    currentOrigin = previous;
  }
}

export function formatMoney(cents) {
  return `¥${(Number(cents || 0) / 100).toFixed(2)}`;
}

export function formatDateTime(timestamp) {
  if (!timestamp) return "-";
  return new Date(Number(timestamp) * 1000).toLocaleString("zh-CN", {
    hour12: false,
  });
}

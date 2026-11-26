export const RUNTIME_NOTICE_PAGE_SCOPE = "stores";

export function runtimeNoticeStorageKey(account) {
  const accountScope = String(
    account?.account_id
    || account?.username
    || "anonymous",
  ).trim() || "anonymous";
  return [
    "langlang",
    "runtime-notice",
    RUNTIME_NOTICE_PAGE_SCOPE,
    encodeURIComponent(accountScope),
  ].join(".");
}

export function readRuntimeNoticeVisible(storage, key) {
  try {
    return storage?.getItem(key) !== "0";
  } catch {
    return true;
  }
}

export function writeRuntimeNoticeVisible(storage, key, visible) {
  try {
    storage?.setItem(key, visible ? "1" : "0");
  } catch {
    // Storage may be disabled by policy; the in-memory switch still works.
  }
}

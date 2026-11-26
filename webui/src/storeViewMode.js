export const STORE_VIEW_OVERVIEW = "overview";
export const STORE_VIEW_FULL = "full";

export function storeViewStorageKey(account) {
  const accountScope = String(
    account?.account_id
    || account?.username
    || "anonymous",
  ).trim() || "anonymous";
  return `langlang.stores.view.${encodeURIComponent(accountScope)}`;
}

export function readStoreViewMode(storage, key) {
  try {
    const value = storage?.getItem(key);
    return value === STORE_VIEW_FULL
      ? STORE_VIEW_FULL
      : STORE_VIEW_OVERVIEW;
  } catch {
    return STORE_VIEW_OVERVIEW;
  }
}

export function writeStoreViewMode(storage, key, value) {
  try {
    storage?.setItem(
      key,
      value === STORE_VIEW_FULL
        ? STORE_VIEW_FULL
        : STORE_VIEW_OVERVIEW,
    );
  } catch {
    // Keep the in-memory selection when storage is unavailable.
  }
}

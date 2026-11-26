export function mergeUploadStoreOptions(existing, incoming) {
  const merged = { ...(existing || {}) };
  for (const [storeId, source] of Object.entries(incoming || {})) {
    merged[storeId] = {
      ...(merged[storeId] || {}),
      ...(source || {}),
    };
  }
  return merged;
}

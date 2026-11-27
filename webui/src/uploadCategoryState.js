const BLOCKED_LABELS = {
  qualification_required: "缺资质",
  deposit_required: "保证金不足",
  rejected: "平台已拒绝",
  unavailable: "不可申请",
  unknown: "状态未知",
};

export function categorySelectable(leaf) {
  if (!leaf || typeof leaf !== "object") return false;
  if (typeof leaf.selectable === "boolean") return leaf.selectable;
  return leaf.available === true;
}

export function categoryApplicationLabel(leaf) {
  const state = String(leaf?.application_state || "");
  if (state === "not_applied") return "未申请，上货时申请";
  if (state === "pending") return "审核中，上货时等待";
  if (state === "effective") return "已生效";
  const provided = String(leaf?.application_label || "").trim();
  return provided || BLOCKED_LABELS[state] || "状态未知";
}

export function categoryApplicationReason(leaf) {
  return String(leaf?.application_reason || "").trim();
}

export function categorySelectionMatchesStore(
  selectionStoreId,
  contextStoreId,
) {
  const selection = String(selectionStoreId || "").trim();
  return !selection || selection === String(contextStoreId || "").trim();
}

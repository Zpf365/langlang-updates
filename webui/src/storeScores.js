export const STORE_SCORE_FIELDS = [
  "dsr",
  "score_product",
  "score_deliver",
  "score_service",
];

function numeric(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function formatScore(value) {
  return numeric(value)?.toFixed(2) ?? "—";
}

function formatCents(value) {
  return `¥${(Number(value || 0) / 100).toFixed(2)}`;
}

function refreshStatus(store, key) {
  return store.score_refresh?.[key] || {};
}

export function scorePresentation(store, key) {
  const label = {
    dsr: "体验分",
    score_product: "商品分",
    score_deliver: "物流分",
    score_service: "服务分",
  }[key] || key;
  const saved = store.score_state?.values?.[key] || {};
  const value = numeric(saved.value ?? store.metrics?.[key]);
  const change = store.score_changes?.[key];
  const refresh = refreshStatus(store, key);
  const delta = numeric(change?.delta);
  const from = numeric(change?.from);
  const to = numeric(change?.to);
  let changeText = "";
  let direction = "";
  let changeTitle = "";
  if (
    value !== null
    && delta
    && delta !== 0
    && from !== null
    && to !== null
  ) {
    direction = delta > 0 ? "up" : "down";
    changeText = delta > 0 ? "↑" : "↓";
    const baselineDate = String(change.baseline_date || "");
    const comparisonDate = String(change.comparison_date || "");
    const officialDates = (
      baselineDate && comparisonDate
        ? ` · 官方日 ${baselineDate} → ${comparisonDate}`
        : ""
    );
    changeTitle = `较昨日：${from.toFixed(2)} → ${to.toFixed(2)} · ${change.updated_at || "时间暂无"}${officialDates}`;
  }
  const status = String(refresh.status || "");
  const statusDetail =
    status === "missing"
      ? "本次接口未返回该指标"
      : status === "failed"
        ? "本次读取失败"
        : status === "pending"
          ? "正在刷新"
          : "";
  const source = String(saved.source || "");
  const updatedAt = String(saved.updated_at || "");
  const valueTitle = value === null
    ? `官方体验分接口没有可用值${statusDetail ? ` · ${statusDetail}` : ""}`
    : `来源：${source || "微信小店体验分接口"}${updatedAt ? ` · 更新：${updatedAt}` : ""}${statusDetail ? ` · ${statusDetail}` : ""}`;
  return {
    label,
    value: value === null ? null : value,
    valueText: formatScore(value),
    valueTitle,
    changeText,
    changeTitle,
    changeDirection: direction,
    statusLabel: "",
    statusTitle: String(refresh.error || ""),
  };
}

export function depositPresentation(store) {
  const meta = store.deposit_meta || {};
  const value = numeric(store.metrics?.deposit);
  const reliable = Boolean(meta.source) && value !== null;
  const status = String(meta.status || "");
  return {
    valueText: reliable ? formatCents(value) : "—",
    detail: reliable
      ? `${meta.source}${meta.updated_at ? ` · 更新 ${meta.updated_at}` : ""}`
      : "官方保证金接口未返回可靠值",
    statusTitle: String(meta.error || ""),
  };
}

const countFormatter = new Intl.NumberFormat("zh-CN");
const moneyFormatter = new Intl.NumberFormat("zh-CN", {
  style: "currency",
  currency: "CNY",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function numeric(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function formatCount(value) {
  return numeric(value) === null ? "—" : countFormatter.format(Number(value));
}

function formatMoney(value) {
  return numeric(value) === null ? "—" : moneyFormatter.format(Number(value) / 100);
}

export function isRefundVisible(amount, orderCount) {
  const amountValue = numeric(amount);
  if (amountValue === null) return true;
  return amountValue !== 0;
}

export function salesPresentation(store, key) {
  const metricKeys = {
    today: "gmv_today",
    yesterday: "gmv_yesterday",
    seven_days: "gmv_7d",
    thirty_days: "gmv_30d",
  };
  const orderKeys = {
    today: "today_orders",
    yesterday: "orders_yesterday",
    seven_days: "orders_7d",
    thirty_days: "orders_30d",
  };
  const refundKeys = {
    today: "refund_gmv_today",
    yesterday: "refund_gmv_yesterday",
    seven_days: "refund_gmv_7d",
    thirty_days: "refund_gmv_30d",
  };
  const value = numeric(store.metrics?.[metricKeys[key]]);
  const orderValue = numeric(store.metrics?.[orderKeys[key]]);
  const refundAmount = numeric(store.metrics?.[refundKeys[key]]);
  const meta = store.sales_meta || {};
  const rawRefundOrderCount = numeric(
    meta.refund_order_counts?.[key],
  );
  const refundCache = meta.refund_order_cache || {};
  const refundConflict = Boolean(
    (refundCache.conflict_periods || []).includes(key)
    || (
      refundAmount !== null
      && rawRefundOrderCount !== null
      && (
        (refundAmount === 0 && rawRefundOrderCount > 0)
        || (refundAmount > 0 && rawRefundOrderCount === 0)
      )
    )
  );
  const refundOrderCount = refundConflict
    ? null
    : rawRefundOrderCount;
  const orderText = orderValue === null
    ? "—单"
    : `${formatCount(orderValue)}单`;
  const refundOrderText = refundOrderCount === null
    ? "—单"
    : `${formatCount(refundOrderCount)}单`;
  const showRefund = isRefundVisible(refundAmount, refundOrderCount);
  return {
    value,
    orderValue,
    refundAmount,
    refundOrderCount,
    showRefund,
    text: formatMoney(value),
    salesText: `销 ${formatMoney(value)} · ${orderText}`,
    refundText: `退 ${formatMoney(refundAmount)} · ${refundOrderText}`,
  };
}

export function exposurePresentation(store, key) {
  const labels = {
    today: "今日曝光",
    yesterday: "昨日曝光",
    seven_days: "7日曝光",
  };
  const legacyPeople = numeric(
    store.exposure_metrics?.[key]
    ?? {
      today: store.metrics?.recommend_exposure,
      yesterday: store.metrics?.recommend_exposure_yesterday,
      seven_days: store.metrics?.recommend_exposure_7d,
    }[key],
  );
  const people = numeric(store.exposure_people?.[key] ?? legacyPeople);
  const count = numeric(store.exposure_counts?.[key]);
  const meta = store.exposure_meta || {};
  const status = String(meta.status?.[key] || "");
  const countStatus = String(meta.count_status?.[key] || "");
  const peopleStatus = String(meta.people_status?.[key] || "");
  const statusText = status === "updated"
    ? "本次已更新"
    : status === "partial"
      ? "本次仅更新部分真实值"
    : status === "missing"
      ? "接口未返回真实值"
      : status === "failed"
        ? "本次读取失败"
        : "等待刷新";
  const sideStatus = (value, itemStatus) => (
    itemStatus === "updated"
      ? "本次已更新"
      : value === null
        ? "暂无真实值"
        : "沿用最后成功值"
  );
  return {
    value: { count, people },
    text: `${formatCount(count)} / ${formatCount(people)}`,
    title:
      `${labels[key]}次数 ${formatCount(count)} · ${labels[key]}人数 ${formatCount(people)}` +
      ` · ${statusText} · 次数${sideStatus(count, countStatus)}` +
      ` · 人数${sideStatus(people, peopleStatus)}` +
      `${meta.updated_at ? ` · 更新 ${meta.updated_at}` : ""}` +
      `${meta.source ? ` · ${meta.source}` : ""}`,
  };
}

export function onlineProductsPresentation(store) {
  const value = numeric(store.online_products);
  const meta = store.online_products_meta || {};
  return {
    value,
    text: value === null ? "—" : countFormatter.format(value),
    title: value === null
      ? `官方在售商品总数不可用${meta.error ? ` · ${meta.error}` : ""}`
      : `${meta.source || "官方在售商品总数"}${meta.updated_at ? ` · 更新 ${meta.updated_at}` : ""}`,
  };
}

export function newProductsTodayPresentation(store) {
  const value = numeric(store.new_products_today);
  const meta = store.new_products_today_meta || {};
  const definition = "今日首次观测上新（Asia/Shanghai 自然日；不代表平台商品创建时间）";
  if (value === null) {
    return {
      value: null,
      text: "—",
      title: `${definition} · 今日尚未完成商品目录扫描`,
    };
  }
  return {
    value,
    text: countFormatter.format(value),
    title: `${definition} · 今日目录扫描已完成${value === 0 ? "，没有新增商品" : ""}`,
  };
}

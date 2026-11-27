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

export function shanghaiDateKey(timestamp = Date.now()) {
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date(timestamp));
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

function isStaleForToday(value, today) {
  const date = String(value || "").trim();
  return Boolean(date && date !== today);
}

export function isRefundVisible(amount, orderCount) {
  const amountValue = numeric(amount);
  const countValue = numeric(orderCount);
  if (amountValue === 0) return false;
  if (amountValue === null && countValue === 0) return false;
  return true;
}

export function salesPresentation(store, key, { today = shanghaiDateKey() } = {}) {
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
  const periodDate = String(
    meta.period_dates?.[key] || meta.as_of_date || "",
  );
  const stale = isStaleForToday(periodDate, today);
  const status = String(meta.status?.[key] || "");
  const source = String(meta.sources?.[key] || meta.source || "");
  const refundStatus = String(meta.refund_amounts?.status?.[key] || "");
  const refundSource = String(
    meta.refund_amounts?.sources?.[key] || "",
  );
  const error = String(meta.error || meta.refund_amounts?.error || "");
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
  const refundOrderCount = refundConflict || stale
    ? null
    : rawRefundOrderCount;
  const orderText = orderValue === null
    ? "—单"
    : `${formatCount(orderValue)}单`;
  const refundOrderText = refundOrderCount === null
    ? "—单"
    : `${formatCount(refundOrderCount)}单`;
  const displayedValue = stale ? null : value;
  const displayedOrderValue = stale ? null : orderValue;
  const displayedRefundAmount = stale ? null : refundAmount;
  const showRefund = !stale && isRefundVisible(
    displayedRefundAmount,
    refundOrderCount,
  );
  const statusText = {
    updated: "本次已更新",
    partial: "本次仅更新部分真实值",
    stale: "沿用最后可信值",
    failed: "本次读取失败",
    missing: "接口未返回真实值",
  }[status] || "等待刷新";
  const refundStatusText = {
    updated: "退款本次已更新",
    stale: "退款沿用最后可信值",
    failed: "退款本次读取失败",
    missing: "退款接口未返回真实值",
  }[refundStatus] || "退款数据状态未知";
  const needsDiagnostics = Boolean(
    stale
    || error
    || (status && status !== "updated")
    || (refundStatus && refundStatus !== "updated"),
  );
  const diagnosticParts = [
    periodDate ? `数据日期 ${periodDate}` : "数据日期未知",
    source ? `来源 ${source}` : "",
    meta.updated_at ? `更新时间 ${meta.updated_at}` : "",
    status ? statusText : "",
    refundSource ? `退款来源 ${refundSource}` : "",
    refundStatus ? refundStatusText : "",
    error ? `错误 ${error}` : "",
  ].filter(Boolean);
  return {
    value: displayedValue,
    orderValue: displayedOrderValue,
    refundAmount: displayedRefundAmount,
    refundOrderCount,
    showRefund,
    stale,
    text: formatMoney(displayedValue),
    salesText: `销 ${formatMoney(displayedValue)} · ${displayedOrderValue === null ? "—单" : orderText}`,
    refundText: `退 ${formatMoney(displayedRefundAmount)} · ${refundOrderText}`,
    title: needsDiagnostics ? diagnosticParts.join(" · ") : undefined,
  };
}

export function exposurePresentation(store, key, { today = shanghaiDateKey() } = {}) {
  const labels = {
    today: "今日商品曝光",
    yesterday: "昨日商品曝光",
    seven_days: "7日商品曝光",
  };
  const meta = store.exposure_meta || {};
  const dataDate = String(
    meta.period_dates?.[key] || meta.as_of_date || "",
  );
  const stale = isStaleForToday(
    dataDate,
    today,
  );
  const people = stale
    ? null
    : numeric(store.product_exposure_people?.[key]);
  const count = stale
    ? null
    : numeric(store.product_exposure_counts?.[key]);
  const clickPeople = stale
    ? null
    : numeric(store.exposure_click_people?.[key]);
  const clickCount = stale
    ? null
    : numeric(store.exposure_click_counts?.[key]);
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
    value: people,
    text: formatCount(people),
    stale,
    title:
      `${labels[key]}人数 ${formatCount(people)}` +
      ` · 商品曝光次数 ${formatCount(count)}` +
      ` · 商品点击人数 ${formatCount(clickPeople)}` +
      ` · 商品点击次数 ${formatCount(clickCount)}` +
      ` · ${statusText} · 曝光人数${sideStatus(people, peopleStatus)}` +
      ` · 曝光次数${sideStatus(count, countStatus)}` +
      `${dataDate ? ` · 数据日期 ${dataDate}` : " · 数据日期未知"}` +
      `${meta.updated_at ? ` · 更新时间 ${meta.updated_at}` : ""}` +
      `${meta.source ? ` · ${meta.source}` : ""}` +
      `${meta.error ? ` · 错误 ${meta.error}` : ""}` +
      `${stale ? " · 数据不是今天，等待刷新" : ""}`,
  };
}

export function onlineProductsPresentation(store, { today = shanghaiDateKey() } = {}) {
  const value = numeric(store.online_products);
  const meta = store.online_products_meta || {};
  const dataDate = String(meta.as_of_date || store.as_of_date || "");
  const stale = isStaleForToday(dataDate, today);
  return {
    value: stale ? null : value,
    text: stale || value === null ? "—" : countFormatter.format(value),
    title: stale
      ? `在线商品数据日期 ${dataDate}，已跨日，等待刷新${meta.source ? ` · ${meta.source}` : ""}${meta.error ? ` · 错误 ${meta.error}` : ""}`
      : value === null
      ? `官方在售商品总数不可用${meta.error ? ` · ${meta.error}` : ""}`
      : `${meta.source || "官方在售商品总数"}${dataDate ? ` · 数据日期 ${dataDate}` : ""}${meta.updated_at ? ` · 更新时间 ${meta.updated_at}` : ""}${meta.error ? ` · 错误 ${meta.error}` : ""}`,
  };
}

export function newProductsTodayPresentation(store, { today = shanghaiDateKey() } = {}) {
  const value = numeric(store.new_products_today);
  const meta = store.new_products_today_meta || {};
  const definition = "今日首次观测上新（Asia/Shanghai 自然日；不代表平台商品创建时间）";
  const dataDate = String(meta.as_of_date || store.as_of_date || "");
  if (!meta.scan_completed_today || value === null || isStaleForToday(dataDate, today)) {
    const lastScan = String(meta.last_scan_completed_at || "").replace("T", " ").slice(0, 19);
    return {
      value: null,
      text: "—",
      title: `${definition} · 今日尚未完成商品目录扫描${lastScan ? ` · 上次完成 ${lastScan}` : ""}`,
    };
  }
  return {
    value,
    text: countFormatter.format(value),
    title: `${definition} · 今日目录扫描已完成${value === 0 ? "，没有新增商品" : ""}`,
  };
}

export function fundPresentation(store, key, { today = shanghaiDateKey() } = {}) {
  const value = numeric(store.metrics?.[key]);
  const meta = store.fund_meta || {};
  const dataDate = String(
    meta.period_dates?.[key] || meta.as_of_date || "",
  );
  const status = String(meta.status?.[key] || "");
  const stale = isStaleForToday(dataDate, today);
  const statusText = {
    updated: "本次已更新",
    stale: "沿用最后可信值",
    failed: "本次读取失败",
    missing: "接口未返回真实值",
  }[status] || "等待刷新";
  return {
    value,
    text: formatMoney(value),
    stale,
    title: [
      dataDate ? `数据日期 ${dataDate}` : "数据日期未知",
      statusText,
      meta.source ? `来源 ${meta.source}` : "",
      meta.updated_at ? `更新时间 ${meta.updated_at}` : "",
      meta.error ? `错误 ${meta.error}` : "",
      stale ? "余额不是今天的数据" : "",
    ].filter(Boolean).join(" · "),
  };
}

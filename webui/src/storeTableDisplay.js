const countFormatter = new Intl.NumberFormat("zh-CN");

function numeric(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function fractionText(hundredths) {
  if (hundredths === 0) return "";
  return hundredths % 10 === 0
    ? `.${hundredths / 10}`
    : `.${String(hundredths).padStart(2, "0")}`;
}

function wanText(hundredths, hasRemainder) {
  const whole = Math.floor(hundredths / 100);
  return `${countFormatter.format(whole)}${fractionText(hundredths % 100)}万${hasRemainder ? "+" : ""}`;
}

function formatCountValue(value) {
  const count = numeric(value);
  if (count === null) return "—";
  const sign = count < 0 ? "-" : "";
  const absolute = Math.abs(count);
  if (absolute >= 10000) {
    return `${sign}${wanText(Math.floor(absolute / 100), absolute % 100 !== 0)}`;
  }
  return `${sign}${countFormatter.format(absolute)}`;
}

function formatCentsValue(value) {
  const cents = numeric(value);
  if (cents === null) return "—";
  const sign = cents < 0 ? "-" : "";
  const absolute = Math.abs(Math.trunc(cents));
  if (absolute >= 1000000) {
    return `${sign}${wanText(Math.floor(absolute / 10000), absolute % 10000 !== 0)}`;
  }
  return `${sign}${countFormatter.format(Math.floor(absolute / 100))}${fractionText(absolute % 100)}`;
}

function pairDisplay(amountValue, countValue, formatAmount) {
  const amount = numeric(amountValue);
  const count = numeric(countValue);
  const amountText = amount === null ? "—" : formatAmount(amount);
  const countText = formatCountValue(count);
  return {
    amount,
    count,
    text: `${amountText}/${countText}`,
    unknown: amount === null && count === null,
    partial: (amount === null) !== (count === null),
  };
}

export function plainMoneyText(value) {
  return formatCentsValue(value);
}

export function salesDisplay(presentation = {}) {
  const sale = pairDisplay(
    presentation.value,
    presentation.orderValue,
    formatCentsValue,
  );
  const refund = pairDisplay(
    presentation.refundAmount,
    presentation.refundOrderCount,
    formatCentsValue,
  );
  const showRefund = Boolean(presentation.showRefund);
  return {
    sale,
    refund,
    salesText: sale.text,
    refundText: refund.text,
    showRefund,
    singleLine: !showRefund,
    salesUnknown: sale.unknown,
    refundUnknown: refund.unknown,
    title: presentation.title || "",
  };
}

export function exposureDisplay(store, key, presentation = {}) {
  const people = presentation.stale
    ? null
    : numeric(store?.product_exposure_people?.[key]);
  const count = presentation.stale
    ? null
    : numeric(store?.product_exposure_counts?.[key]);
  const exposed = pairDisplay(
    people,
    count,
    formatCountValue,
  );
  return {
    people: exposed.amount,
    count: exposed.count,
    text: exposed.text,
    unknown: exposed.unknown,
    partial: exposed.partial,
    title: presentation.title || "",
  };
}

export function depositDisplay(store) {
  const value = numeric(store?.metrics?.deposit);
  const meta = store?.deposit_meta || {};
  const reliable = Boolean(meta.source) && value !== null;
  return {
    text: reliable ? plainMoneyText(value) : "—",
    title: [
      reliable ? meta.source : "官方保证金接口未返回可靠值",
      meta.updated_at ? `更新 ${meta.updated_at}` : "",
      meta.error ? `错误 ${meta.error}` : "",
    ].filter(Boolean).join(" · "),
  };
}

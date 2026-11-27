const countFormatter = new Intl.NumberFormat("zh-CN");
const amountFormatter = new Intl.NumberFormat("zh-CN", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function numeric(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function pairDisplay(amountValue, countValue, formatAmount) {
  const amount = numeric(amountValue);
  const count = numeric(countValue);
  const amountText = amount === null ? "—" : formatAmount(amount);
  const countText = count === null ? "—" : countFormatter.format(count);
  return {
    amount,
    count,
    text: `${amountText}/${countText}`,
    unknown: amount === null && count === null,
    partial: (amount === null) !== (count === null),
  };
}

export function plainMoneyText(value) {
  const amount = numeric(value);
  return amount === null ? "—" : amountFormatter.format(amount / 100);
}

export function salesDisplay(presentation = {}) {
  const sale = pairDisplay(
    presentation.value,
    presentation.orderValue,
    (value) => amountFormatter.format(value / 100),
  );
  const refund = pairDisplay(
    presentation.refundAmount,
    presentation.refundOrderCount,
    (value) => amountFormatter.format(value / 100),
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
    (value) => countFormatter.format(value),
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

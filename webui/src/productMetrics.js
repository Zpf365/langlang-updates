const numberFormatter = new Intl.NumberFormat("zh-CN");

export function productMetricText(value) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  return Number.isFinite(number) ? numberFormatter.format(number) : "—";
}

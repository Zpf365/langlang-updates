export const PRODUCT_SORT_COLUMNS = {
  title: { type: "text" },
  product_id: { type: "text" },
  status_text: { type: "text" },
  min_price: { type: "number" },
  stock_total: { type: "number" },
  sold30: { type: "number" },
  views30: { type: "number" },
  recommend_views30: { type: "number" },
  total_sales: { type: "number" },
  review_count: { type: "number" },
};

function missing(value) {
  return value === null || value === undefined || value === "";
}

export function nextProductSort(current, key) {
  if (!current.key || current.key !== key) return { key, direction: "asc" };
  if (current.direction === "asc") return { key, direction: "desc" };
  return { key: "", direction: "" };
}

export function compareProductValues(left, right, key) {
  const definition = PRODUCT_SORT_COLUMNS[key];
  if (!definition) return 0;
  const leftMissing = missing(left);
  const rightMissing = missing(right);
  if (leftMissing && rightMissing) return 0;
  if (leftMissing) return 1;
  if (rightMissing) return -1;
  if (definition.type === "number") {
    return Number(left) - Number(right);
  }
  return String(left).localeCompare(String(right), "zh-CN", {
    numeric: true,
    sensitivity: "base",
  });
}

export function sortProducts(rows, sort) {
  if (!sort.key || !sort.direction) return [...rows];
  const direction = sort.direction === "desc" ? -1 : 1;
  return [...rows].sort((left, right) => {
    const leftValue = left[sort.key];
    const rightValue = right[sort.key];
    const leftMissing = missing(leftValue);
    const rightMissing = missing(rightValue);
    if (leftMissing || rightMissing) {
      return compareProductValues(leftValue, rightValue, sort.key);
    }
    return compareProductValues(leftValue, rightValue, sort.key) * direction;
  });
}

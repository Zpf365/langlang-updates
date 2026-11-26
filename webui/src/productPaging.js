export const DEFAULT_PRODUCT_PAGE_SIZE = 50;
export const MAX_PRODUCT_PAGE_SIZE = 10000;

export function parseProductPageSize(value, previous = DEFAULT_PRODUCT_PAGE_SIZE) {
  const text = String(value ?? "").trim();
  if (!text) {
    return { value: previous, error: "每页数量不能为空" };
  }
  if (!/^\d+$/.test(text)) {
    return { value: previous, error: "每页数量必须是正整数" };
  }
  const size = Number(text);
  if (!Number.isSafeInteger(size) || size < 1 || size > MAX_PRODUCT_PAGE_SIZE) {
    return {
      value: previous,
      error: `每页数量必须在 1–${MAX_PRODUCT_PAGE_SIZE} 之间`,
    };
  }
  return { value: size, error: "" };
}

export function productPageCount(totalItems, pageSize) {
  return Math.max(1, Math.ceil(Math.max(0, Number(totalItems) || 0) / pageSize));
}

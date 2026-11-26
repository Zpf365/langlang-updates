import { matchesOrderSearch } from "./orderSearch";

export function dateBoundary(value, endOfDay = false) {
  if (!value) return 0;
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return 0;
  return Math.floor(date.getTime() / 1000) + (endOfDay ? 86400 : 0);
}

export function parseHiddenProductIds(value) {
  return new Set(
    String(value || "")
      .split(/[\s,，;；]+/)
      .map((item) => item.trim())
      .filter(Boolean),
  );
}

export function filterOrdersForTabScope(orders, filters, hiddenProductIdsValue) {
  const hiddenIds = parseHiddenProductIds(hiddenProductIdsValue);
  const startTime = dateBoundary(filters.start_date, false);
  const endTime = dateBoundary(filters.end_date, true);

  return (orders || []).filter((order) => {
    if (hiddenIds.has(String(order.item_product_id || ""))) return false;
    if (filters.store_id && String(order.store_id) !== String(filters.store_id)) {
      return false;
    }
    if (filters.mark === "已标记" && !order.is_marked) return false;
    if (filters.mark === "未标记" && order.is_marked) return false;
    if (startTime && Number(order.create_time || 0) < startTime) return false;
    if (endTime && Number(order.create_time || 0) >= endTime) return false;
    return matchesOrderSearch(order, filters.search);
  });
}

export function matchesOrderTab(order, tabKey) {
  if (!tabKey) return true;
  if (tabKey === "wait_purchase") {
    return Boolean(order.needs_procurement)
      || ["unlinked", "wait_purchase", "error"].includes(
        String(order.procurement_state || ""),
      );
  }
  if (tabKey === "20") return [17, 20].includes(Number(order.status));
  if (tabKey === "after_sale") {
    return Boolean(
      String(order.status_text || "").match(/售后|退款/)
      || order.after_sale_count,
    );
  }
  if (tabKey === "marked") return Boolean(order.is_marked);
  return String(order.status) === tabKey;
}

export function countOrdersForTab(scopeOrders, tabKey) {
  if (!tabKey) return (scopeOrders || []).length;
  return (scopeOrders || []).filter((order) => matchesOrderTab(order, tabKey)).length;
}

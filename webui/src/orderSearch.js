export function parseOrderSearch(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .split(/[\s,，;；]+/)
    .filter(Boolean);
}

export function orderSearchValues(order) {
  return [
    order.order_id,
    order.buyer_name,
    order.phone,
    order.item_summary,
    order.store_name,
    order.waybill_id,
    order.purchase_order_no,
    order.pickup_code,
    ...(order.purchase_orders || []).flatMap((purchase) => [
      purchase.external_order_id,
      purchase.mail_no,
    ]),
  ].map((value) => String(value || "").toLowerCase());
}

export function matchesOrderSearch(order, value) {
  const terms = parseOrderSearch(value);
  if (!terms.length) return true;
  const searchable = orderSearchValues(order);
  return terms.some((term) => searchable.some((item) => item.includes(term)));
}

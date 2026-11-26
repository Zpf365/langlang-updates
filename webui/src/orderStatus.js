export const ORDER_STATUS_LABELS = {
  10: "待付款",
  12: "礼物待收下",
  13: "待成团",
  17: "待核销",
  20: "待发货",
  21: "部分发货",
  30: "待收货",
  100: "已完成",
  200: "售后退款完成",
  250: "已取消",
};

const WORKBENCH_STAGE_LABELS = {
  pending_purchase: "待采购",
  purchased: "已采购待发货",
  purchased_pending_shipment: "已采购待发货",
  shipped: "已发货",
  completed: "已完成",
  exception: "异常",
  refunding: "退款中",
  closed: "交易关闭",
};

export function orderStatusLabel(order = {}) {
  const status = Number(order.status);
  return ORDER_STATUS_LABELS[status] || String(order.status_text || "状态未知");
}

export function fulfillmentStageLabel(order = {}) {
  const status = Number(order.status);
  if (status === 30 && order.fulfillment_stage === "shipped") {
    return "已发货";
  }
  if ([21, 30, 100, 200, 250].includes(status)) {
    return orderStatusLabel(order);
  }
  return WORKBENCH_STAGE_LABELS[order.fulfillment_stage]
    || orderStatusLabel(order);
}

export function shippingCountdown(order = {}, nowSeconds = 0) {
  if (Number(order.status) !== 20) return "";
  const deadline = Number(order.shipping_deadline_at || 0);
  const current = Number(nowSeconds);
  if (!(deadline > 0) || !Number.isFinite(current)) return "";
  const seconds = deadline - current;
  const overdue = seconds <= 0;
  if (overdue) return "已超时";
  const remaining = Math.abs(seconds);
  const days = Math.floor(remaining / 86400);
  const hours = Math.floor((remaining % 86400) / 3600);
  const minutes = Math.floor((remaining % 3600) / 60);
  const duration = [
    days ? `${days}天` : "",
    hours ? `${hours}小时` : "",
    minutes ? `${minutes}分` : "",
  ].filter(Boolean).join("") || (
    remaining > 0 && remaining < 60 ? "1分" : ""
  );
  return `剩余 ${duration}`;
}

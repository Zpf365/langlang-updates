import {
  accountsForLink,
  detectProcurementPlatform,
  platformLabel,
  sourceProductId,
} from "./procurementLink";

export const PURCHASE_READBACK_STATUSES = new Set([
  "page_open_pending_payment",
  "verification_pending",
]);

function text(value) {
  return String(value ?? "").trim();
}

export function purchaseGate({
  url = "",
  accountId = "",
  accounts = [],
  addressReady = false,
} = {}) {
  const reasons = [];
  const platform = detectProcurementPlatform(url);
  const sourceId = sourceProductId(url);
  if (!text(url)) {
    reasons.push("请粘贴淘宝或 1688 商品链接");
  } else if (!platform || !sourceId) {
    reasons.push("链接不是有效的淘宝、天猫或 1688 商品地址");
  }

  const matching = accountsForLink(url, accounts);
  const selected = matching.find(
    (account) => String(account?.id) === String(accountId),
  ) || matching.find(
    (account) => text(account?.status) === "已登录",
  ) || matching[0] || null;

  if (platform && !matching.length) {
    reasons.push(`没有对应的${platformLabel(platform)}采购账号`);
  } else if (selected && text(selected.status) !== "已登录") {
    reasons.push(
      `${platformLabel(platform)}采购账号未登录，请先完成官方登录`,
    );
  } else if (platform && !selected) {
    reasons.push(`请选择已登录的${platformLabel(platform)}采购账号`);
  }
  if (!addressReady) {
    reasons.push("采购地址不完整，请先补全收件人、电话和详细地址");
  }
  return {
    valid: reasons.length === 0,
    platform,
    sourceProductId: sourceId,
    account: selected,
    accountId: selected?.id || "",
    reasons,
    reason: reasons.join("；"),
  };
}

export function canRequestPurchaseReadback(link = {}) {
  if (link.purchase_confirmed) return false;
  return PURCHASE_READBACK_STATUSES.has(
    text(link.purchase_attempt_status),
  );
}

export function purchaseReadbackPayload(order = {}, link = {}, accountId = "") {
  return {
    store_id: order.store_id,
    order_id: order.order_id,
    product_id: text(link.product_id),
    link_id: Number(link.id || 0),
    account_id: Number(accountId || link.account_id || 0),
  };
}

export function confirmedPurchaseSummary(links = []) {
  const records = new Map();
  for (const link of links || []) {
    const record = link?.purchase_record;
    if (!record) continue;
    const key = text(record.upstream_order_id)
      || text(record.trade_no)
      || `link-${link.id}`;
    if (!records.has(key)) {
      records.set(key, {
        ...record,
        upstream_order_id: key,
        paid_amount: Number(record.paid_amount || 0),
        freight_amount: Number(record.freight_amount || 0),
      });
    }
  }
  const rows = [...records.values()];
  return {
    count: rows.length,
    paid: rows.reduce((total, row) => total + row.paid_amount, 0),
    freight: rows.reduce((total, row) => total + row.freight_amount, 0),
    cost: rows.reduce(
      (total, row) => total + row.paid_amount + row.freight_amount,
      0,
    ),
    rows,
  };
}

export function scopePurchaseEvidenceToLinks(links = []) {
  const counts = new Map();
  for (const link of links || []) {
    const productId = text(link?.product_id);
    counts.set(productId, Number(counts.get(productId) || 0) + 1);
  }
  return (links || []).map((link) => {
    const productId = text(link?.product_id);
    const linkIsProvable = (
      Number(counts.get(productId) || 0) === 1
      || text(link?.purchase_attempt_status) === "confirmed"
    );
    if (linkIsProvable || (!link?.purchase_confirmed && !link?.purchase_record)) {
      return link;
    }
    return {
      ...link,
      purchase_confirmed: false,
      purchase_record: null,
      purchase_attempt_error: "同一商品存在多个采购链接，当前采购记录无法唯一归属；请重新打开对应链接并回读",
    };
  });
}

export function supplierShipments(order = {}) {
  const rows = [];
  const seen = new Set();
  const add = (row) => {
    const waybillId = text(row.waybill_id);
    if (!waybillId || seen.has(waybillId)) return;
    seen.add(waybillId);
    rows.push({
      waybill_id: waybillId,
      delivery_id: text(row.delivery_id),
      carrier: text(row.carrier),
      status_text: text(row.status_text),
      source: text(row.source || "controller"),
    });
  };
  for (const row of order.purchase_orders || []) {
    add({
      waybill_id: row.mail_no,
      delivery_id: row.carrier,
      carrier: row.carrier,
      status_text: row.status_text,
      source: "采购回读",
    });
  }
  for (const task of order.procurement_tasks || []) {
    add({
      waybill_id: task.tracking_no,
      delivery_id: task.tracking_company,
      carrier: task.tracking_company,
      status_text: task.status_text || task.status,
      source: "调度任务",
    });
  }
  return rows;
}

export function officialDeliveryAction(order = {}) {
  const status = Number(order.status || 0);
  const hasOfficialWaybill = (
    text(order.waybill_id)
    || Boolean(order.packages?.length)
  );
  const shipments = supplierShipments(order);
  if (hasOfficialWaybill || [21, 30, 100].includes(status)) {
    return {
      mode: "update",
      label: "更新官方物流",
      reason: "订单已发货，可核对并更新官方物流信息",
      shipments,
    };
  }
  if (order.procurement_state === "aftersale") {
    return {
      mode: "wait",
      label: "",
      reason: "订单处于售后流程，已阻止自动或人工发货",
      shipments: [],
    };
  }
  if (order.procurement_state === "error") {
    return {
      mode: "manual",
      label: "人工处理发货",
      reason: "采购或回读任务异常，需要人工核对",
      shipments: [],
    };
  }
  if (shipments.length) {
    return {
      mode: "official",
      label: "官方发货 / 回填",
      reason: "上家已发货，可把真实运单号回填到微信小店",
      shipments,
    };
  }
  return {
    mode: "wait",
    label: "",
    reason: order.procurement_state === "wait_supplier"
      ? "等待上家发货并回读运单号"
      : "",
    shipments: [],
  };
}

export function isOfficialDeliveryCode(value) {
  return /^[A-Za-z0-9_-]{2,32}$/.test(text(value));
}

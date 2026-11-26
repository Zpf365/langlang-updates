import { describe, expect, it } from "vitest";
import {
  canRequestPurchaseReadback,
  confirmedPurchaseSummary,
  isOfficialDeliveryCode,
  officialDeliveryAction,
  purchaseGate,
  purchaseReadbackPayload,
  scopePurchaseEvidenceToLinks,
  supplierShipments,
} from "./orderProcurementFlow";

const accounts = [
  { id: 1, platform: "淘宝", label: "淘宝采购", status: "已登录" },
  { id: 2, platform: "1688", label: "1688采购", status: "已登录" },
  { id: 3, platform: "1688", label: "1688失效", status: "登录已失效" },
];

describe("order procurement flow gates", () => {
  it("only opens an official page with a valid same-platform logged-in account and complete address", () => {
    const valid = purchaseGate({
      url: "https://detail.1688.com/offer/1048048297681.html",
      accountId: 2,
      accounts,
      addressReady: true,
    });
    expect(valid.valid).toBe(true);
    expect(valid.accountId).toBe(2);

    const blocked = purchaseGate({
      url: "https://item.taobao.com/item.htm?id=1012686581380",
      accountId: 3,
      accounts: [
        { id: 4, platform: "淘宝", label: "淘宝失效", status: "登录已失效" },
      ],
      addressReady: false,
    });
    expect(blocked.valid).toBe(false);
    expect(blocked.reason).toContain("未登录");
    expect(blocked.reason).toContain("采购地址不完整");
  });

  it("rejects invalid links and exposes the exact purchase readback payload", () => {
    const rejected = purchaseGate({
      url: "https://example.com/item?id=1",
      accounts,
      addressReady: true,
    });
    expect(rejected.valid).toBe(false);
    expect(rejected.reason).toContain("链接不是有效");

    expect(purchaseReadbackPayload(
      { store_id: 9, order_id: "WX-1" },
      { id: 18, product_id: "P-1", account_id: 2 },
      2,
    )).toEqual({
      store_id: 9,
      order_id: "WX-1",
      product_id: "P-1",
      link_id: 18,
      account_id: 2,
    });
  });

  it("offers one readback step only after the official page was opened", () => {
    expect(canRequestPurchaseReadback({
      purchase_attempt_status: "page_open_pending_payment",
    })).toBe(true);
    expect(canRequestPurchaseReadback({
      purchase_attempt_status: "verification_pending",
    })).toBe(true);
    expect(canRequestPurchaseReadback({
      purchase_attempt_status: "page_open_pending_payment",
      purchase_confirmed: true,
    })).toBe(false);
    expect(canRequestPurchaseReadback({})).toBe(false);
  });
});

describe("controller-backed purchase and supplier shipping", () => {
  it("uses only controller purchase records for actual cost and order identity", () => {
    const summary = confirmedPurchaseSummary([
      {
        id: 1,
        purchase_record: {
          upstream_order_id: "1688-1",
          paid_amount: 4680,
          freight_amount: 200,
        },
      },
      {
        id: 2,
        purchase_record: {
          upstream_order_id: "1688-1",
          paid_amount: 4680,
          freight_amount: 200,
        },
      },
      {
        id: 3,
        purchase_record: {
          upstream_order_id: "TB-2",
          paid_amount: 3000,
          freight_amount: 0,
        },
      },
    ]);
    expect(summary.count).toBe(2);
    expect(summary.paid).toBe(7680);
    expect(summary.cost).toBe(7880);
  });

  it("does not copy one product-level purchase record onto multiple candidate links", () => {
    const scoped = scopePurchaseEvidenceToLinks([
      {
        id: 1,
        product_id: "P-1",
        purchase_confirmed: true,
        purchase_attempt_status: "confirmed",
        purchase_record: { upstream_order_id: "1688-1" },
      },
      {
        id: 2,
        product_id: "P-1",
        purchase_confirmed: true,
        purchase_attempt_status: "",
        purchase_record: { upstream_order_id: "1688-1" },
      },
      {
        id: 3,
        product_id: "P-2",
        purchase_confirmed: true,
        purchase_attempt_status: "",
        purchase_record: { upstream_order_id: "1688-2" },
      },
    ]);
    expect(scoped[0].purchase_record.upstream_order_id).toBe("1688-1");
    expect(scoped[1].purchase_record).toBeNull();
    expect(scoped[1].purchase_attempt_error).toContain("无法唯一归属");
    expect(scoped[2].purchase_record.upstream_order_id).toBe("1688-2");
  });

  it("routes real supplier tracking to official delivery and leaves exceptions manual", () => {
    const shipped = {
      status: 20,
      procurement_state: "auto_filling",
      purchase_orders: [{
        mail_no: "SF1234567890",
        carrier: "SF",
        status_text: "已发货",
      }],
      procurement_tasks: [],
    };
    expect(supplierShipments(shipped)).toEqual([expect.objectContaining({
      waybill_id: "SF1234567890",
      delivery_id: "SF",
    })]);
    expect(officialDeliveryAction(shipped)).toMatchObject({
      mode: "official",
      label: "官方发货 / 回填",
    });
    expect(officialDeliveryAction({
      status: 20,
      procurement_state: "error",
      purchase_orders: [],
      procurement_tasks: [{ status: "error", tracking_no: "" }],
    }).label).toBe("人工处理发货");
    expect(officialDeliveryAction({
      status: 20,
      procurement_state: "error",
      purchase_orders: [{ mail_no: "OLD-TRACK", carrier: "SF" }],
      procurement_tasks: [],
    }).mode).toBe("manual");
    expect(officialDeliveryAction({
      status: 20,
      procurement_state: "aftersale",
      purchase_orders: [{ mail_no: "OLD-TRACK", carrier: "SF" }],
      procurement_tasks: [],
    }).mode).toBe("wait");
    expect(officialDeliveryAction({
      status: 20,
      procurement_state: "wait_supplier",
      purchase_orders: [],
      procurement_tasks: [],
    }).mode).toBe("wait");
  });

  it("requires a real platform courier code instead of a Chinese carrier name", () => {
    expect(isOfficialDeliveryCode("SF")).toBe(true);
    expect(isOfficialDeliveryCode("ZTO")).toBe(true);
    expect(isOfficialDeliveryCode("中通")).toBe(false);
  });
});

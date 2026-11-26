// Offline regression coverage for procurement link identity and account binding.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  chooseLinkAccount,
  detectProcurementPlatform,
  linkValidation,
  sourceProductId,
  usableSourceSkus,
} from "./procurementLink.js";

const dialog = readFileSync(
  new URL("./components/OrderProcurementDialog.vue", import.meta.url),
  "utf8",
);

const accounts = [
  { id: 1, platform: "淘宝", label: "淘宝号", status: "已登录" },
  { id: 2, platform: "1688", label: "1688号", status: "已登录" },
  { id: 3, platform: "1688", label: "备用1688", status: "登录已失效" },
];

describe("procurement link platform and account binding", () => {
  it("recognizes Taobao/Tmall and 1688 target product ids", () => {
    expect(detectProcurementPlatform(
      "https://item.taobao.com/item.htm?id=1012686581380",
    )).toBe("taobao");
    expect(detectProcurementPlatform(
      "https://detail.tmall.com/item.htm?id=1012686581380",
    )).toBe("taobao");
    expect(detectProcurementPlatform(
      "https://detail.1688.com/offer/1048048297681.html",
    )).toBe("1688");
    expect(sourceProductId(
      "https://item.taobao.com/item.htm?id=1012686581380",
    )).toBe("1012686581380");
    expect(sourceProductId(
      "https://detail.1688.com/offer/1048048297681.html",
    )).toBe("1048048297681");
  });

  it("automatically chooses only a logged-in account on the same platform", () => {
    expect(chooseLinkAccount(
      "https://item.taobao.com/item.htm?id=1012686581380",
      accounts,
    )?.id).toBe(1);
    expect(chooseLinkAccount(
      "https://detail.1688.com/offer/1048048297681.html",
      accounts,
    )?.id).toBe(2);
  });

  it("disables reading when no matching platform account exists", () => {
    const state = linkValidation({
      url: "https://item.taobao.com/item.htm?id=1012686581380",
      account_id: "",
    }, [accounts[1]]);
    expect(state.valid).toBe(false);
    expect(state.platform).toBe("taobao");
    expect(state.reason).toContain("淘宝账号");
  });

  it("rejects placeholder, duplicated, unpriced and stockless source skus", () => {
    const rows = usableSourceSkus([
      {
        sku_id: "real-1",
        label: "黑色 / XL",
        price: 4680,
        stock: 20,
      },
      {
        sku_id: "real-1",
        label: "黑色 / XL",
        price: 4680,
        stock: 20,
      },
      {
        sku_id: "1688-offer-1",
        label: "默认规格",
        price: 4680,
        stock: 0,
      },
      {
        sku_id: "missing-stock",
        label: "白色 / L",
        stock: null,
      },
      {
        sku_id: "missing-price",
        label: "灰色 / M",
        price: null,
        stock: 6,
      },
    ]);
    expect(rows).toHaveLength(1);
    expect(rows[0].sku_id).toBe("real-1");
  });

  it("persists links directly and opens purchase without sku matching", () => {
    expect(dialog).toContain("本地原始 SKU");
    expect(dialog).toContain("保存链接");
    expect(dialog).toContain("打开购买");
    expect(dialog).not.toContain("已完成采购，开始核验");
    expect(dialog).toContain("orders.procurement.link-upsert");
    expect(dialog).toContain("orders.procurement.purchase-open");
    expect(dialog).not.toContain("orders.procurement.purchase-confirm");
    expect(dialog).not.toContain("确认已付款并回读");
    expect(dialog).not.toContain("请手动点击");
    expect(dialog).toContain(
      "orders.procurement.purchase-auto-verify.status",
    );
    expect(dialog).toContain("付款完成后软件会自动有限次核对");
    expect(dialog).toContain("purchase_attempt_status");
    expect(dialog).toContain("采购页面已打开 / 待用户付款");
    expect(dialog).not.toContain("setInterval(");
    expect(dialog).not.toContain("list_bought_items");
    expect(dialog).not.toContain("buyer_order_list");
    expect(dialog).not.toContain("member1.taobao.com");
    expect(dialog).toContain("product_purchase_links");
    expect(dialog).toContain("onLinkUrlInput");
    expect(dialog).toContain("accountsForLink");
    expect(dialog).toContain("procurement-order-rail");
    expect(dialog).toContain("procurement-link-pane");
    expect(dialog).toContain("openProductPreview");
    expect(dialog).toContain("cycleProductPreview");
    expect(dialog).toContain("点击放大");
    expect(dialog).toContain("点击缩小");
    expect(dialog).toContain("点击隐藏");
    expect(dialog).not.toContain("读取并匹配");
    expect(dialog).not.toContain("确认关联");
    expect(dialog).not.toContain("orders.procurement.product-preview");
    expect(dialog).not.toContain('meta[property="og:title"]');
  });
});

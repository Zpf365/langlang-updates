import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const orderPage = readFileSync(
  new URL("./pages/OrdersParityPage.vue", import.meta.url),
  "utf8",
);
const orderRow = readFileSync(
  new URL("./components/OrderTableRow.vue", import.meta.url),
  "utf8",
);
const productsPage = readFileSync(
  new URL("./pages/ProductsPage.vue", import.meta.url),
  "utf8",
);
const procurementPage = readFileSync(
  new URL("./pages/ProcurementPage.vue", import.meta.url),
  "utf8",
);

describe("order flag, logistics and product navigation", () => {
  it("renders the official flag color swatch without using a fixed color", () => {
    expect(orderRow).toContain('class="note-color-swatch"');
    expect(orderRow).toContain(':class="noteFlagClass"');
    expect(orderRow).toContain("official_note_color_code");
    expect(orderRow).toContain("flag-red");
    expect(orderRow).toContain("flag-orange");
    expect(orderRow).toContain("flag-green");
    expect(orderRow).toContain("flag-blue");
    expect(orderRow).toContain("无旗帜 · 无备注");
    expect(orderPage).toContain(">无色</button>");
  });

  it("makes every package waybill clickable and independently copyable", () => {
    expect(orderRow).toContain('v-for="(packageRow, index) in logisticsPackages"');
    expect(orderRow).toContain('call("orders.logistics.track"');
    expect(orderRow).toContain('copyText(value)');
    expect(orderRow).toContain("waybill-copy");
    expect(orderRow).toContain("官方物流轨迹");
  });

  it("opens the exact store and product from the order product title", () => {
    expect(orderRow).toContain('class="product-title-button"');
    expect(orderRow).toContain('@click="emit(\'product\', order)"');
    expect(orderRow).toContain('class="product-thumb"');
    expect(orderRow).toContain('@click="emit(\'preview\', order.item_thumb_url)"');
    expect(orderPage).toContain('@product="openOrderProduct"');
    expect(orderPage).toContain('call("orders.refresh-one"');
    expect(orderPage).toContain("#/managed-products?");
    expect(orderPage).toContain("store_id: String(order.store_id)");
    expect(orderPage).toContain("product_id: productId");
    expect(productsPage).toContain('call("products.locate"');
    expect(productsPage).toContain("focused-product-row");
    expect(productsPage).toContain("定位商品失败");
  });
});

describe("procurement account login flow", () => {
  it("uses an async login job and exposes real errors", () => {
    expect(procurementPage).toContain('call("procurement.login.start"');
    expect(procurementPage).toContain('call("procurement.login.status"');
    expect(procurementPage).toContain("job.status === \"failed\"");
    expect(procurementPage).toContain("登录会话已安全保存");
  });

  it("checks persisted sessions on page entry", () => {
    expect(procurementPage).toContain("probeSavedSessions");
    expect(procurementPage).toContain('call("procurement.probe"');
    expect(procurementPage).toContain("account.has_session");
  });
});

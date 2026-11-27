import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const ordersPage = readFileSync(
  new URL("./pages/OrdersParityPage.vue", import.meta.url),
  "utf8",
).replaceAll("\r\n", "\n");
const orderRow = readFileSync(
  new URL("./components/OrderTableRow.vue", import.meta.url),
  "utf8",
).replaceAll("\r\n", "\n");
const clipboard = readFileSync(
  new URL("./clipboard.js", import.meta.url),
  "utf8",
);
const orderTabScope = readFileSync(
  new URL("./orderTabScope.js", import.meta.url),
  "utf8",
);

describe("order refresh and clipboard contract", () => {
  it("removes the import-address workflow and file control", () => {
    expect(ordersPage).not.toContain("导入地址");
    expect(ordersPage).not.toContain("addressFileInput");
    expect(ordersPage).not.toContain("选择文件");
    expect(orderRow).toContain('class="hidden-file-input"');
    expect(orderRow).toContain("hidden\n    aria-hidden=\"true\"");
    expect(orderRow).toContain(".hidden-file-input {\n  display: none !important;");
  });

  it("uses update-time refresh with current page order ids", () => {
    expect(ordersPage).toContain("current_order_ids");
    expect(ordersPage).toContain("status_changes");
    expect(ordersPage).toContain("await syncOrders({ automatic: true })");
    expect(ordersPage).toContain('call("orders.after-sales.sync"');
    expect(ordersPage).toContain('from "../orderTabScope"');
    expect(orderTabScope).toContain('[17, 20].includes(Number(order.status))');
  });

  it("uses the native clipboard bridge before execCommand fallback", () => {
    expect(orderRow).toContain("copyText(");
    expect(orderRow).not.toContain("navigator.clipboard.writeText");
    expect(clipboard).toContain('call("clipboard.write"');
    expect(clipboard).toContain('document.execCommand("copy")');
  });
});

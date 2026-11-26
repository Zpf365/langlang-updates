import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const page = readFileSync(
  new URL("./pages/OrdersParityPage.vue", import.meta.url),
  "utf8",
);
const row = readFileSync(
  new URL("./components/OrderTableRow.vue", import.meta.url),
  "utf8",
);
const procurementDialog = readFileSync(
  new URL("./components/OrderProcurementDialog.vue", import.meta.url),
  "utf8",
);
const countdownClock = readFileSync(
  new URL("./orderCountdownClock.js", import.meta.url),
  "utf8",
);

describe("single-page order workspace contract", () => {
  it("keeps a synchronized bottom slider and sticky order identity", () => {
    expect(page).toContain('ref="orderTableScroll"');
    expect(page).toContain('class="order-horizontal-range"');
    expect(page).toContain("@input=\"moveHorizontal($event.target.value)\"");
    expect(page).toContain('class="col-check sticky-check"');
    expect(page).toContain('class="col-order sticky-order"');
    expect(row).toContain("position: sticky;");
    expect(row).toContain("grid-template-columns: 76px minmax(0, 1fr);");
  });

  it("keeps order actions inline and removes the workbench import", () => {
    expect(page).toContain("<OrderTableRow");
    expect(page).not.toContain("OrderWorkbenchPanel");
    for (const endpoint of [
      "orders.workbench.detail",
      "orders.workbench.manual-purchase",
      "orders.workbench.purchase-focus",
      "orders.workbench.browse-procurement",
      "orders.workbench.sync-procurement",
      "orders.workbench.sync-procurement-note",
      "orders.workbench.address-update",
      "orders.workbench.price-update",
      "orders.workbench.delivery-update",
      "orders.after-sales.list",
      "orders.after-sales.action",
      "orders.workbench.logs",
    ]) {
      expect(row).toContain(endpoint);
    }
    expect(row).toContain("<OrderProcurementDialog");
    expect(procurementDialog).toContain("orders.procurement.link-upsert");
    expect(procurementDialog).toContain("orders.procurement.link-delete");
    expect(procurementDialog).toContain("orders.procurement.purchase-open");
    expect(procurementDialog).toContain("打开购买");
    expect(procurementDialog).not.toContain("已完成采购，开始核验");
    expect(procurementDialog).toContain(
      "orders.procurement.purchase-confirm",
    );
    expect(procurementDialog).toContain("确认已付款并回读");
    expect(procurementDialog).toContain("purchase_attempt_status");
    expect(procurementDialog).not.toContain("setInterval(");
    expect(procurementDialog).toContain(
      'width="min(1580px, calc(100vw - 28px))"',
    );
    expect(procurementDialog).toContain("link-url");
    expect(procurementDialog).toContain(
      "grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) 112px 72px",
    );
    expect(row).toContain("orders.address.save-software");
    expect(row).toContain('rows="8"');
    expect(row).toContain("address-raw-textarea");
    expect(row).toContain("输入停止约半秒后自动解析");
    expect(row).toContain("current?.procurement_address");
    expect(row).toContain("procurementAddressForm(detail.value, props.order)");
    expect(row).toContain("procurementAddressText(addressForm)");
    expect(row).not.toContain("极速下单（未接入）");
  });

  it("exposes e-waybill and after-sale proof actions in the real routed order row", () => {
    expect(row).toContain('call("orders.workbench.shipping-options"');
    expect(row).toContain('call("orders.workbench.shipment-create"');
    expect(row).toContain('call("orders.workbench.shipment-complete"');
    expect(row).toContain('call("orders.after-sales.upload-media"');
    expect(row).toContain("media_files: afterSaleMediaIds[row.after_sale_order_id]");
    expect(row).toContain("生成电子面单");
    expect(row).toContain("完成面单发货");
    expect(row).toContain("上传凭证");
    expect(page).toContain('call("orders.workbench.open-official.status"');
    expect(page).toContain("job.status === \"completed\"");
    expect(page).toContain("job.status === \"failed\"");
  });

  it("collects the required old-platform parameters for after-sale actions", () => {
    for (const operation of [
      "reject",
      "negotiate",
      "reship",
      "update_reship",
      "accept_exchange",
      "reject_exchange",
    ]) {
      expect(row).toContain(`'${operation}'`);
    }
    expect(row).toContain("reject_reason_type");
    expect(row).toContain("merchant_update_type");
    expect(row).toContain("update_reason_type");
    expect(row).toContain("merchant_update_desc");
    expect(row).toContain("delivery_id");
    expect(row).toContain("waybill_id");
  });

  it("opens the routed order settlement dialog from logistics", () => {
    expect(page).toContain('route.query.order_id');
    expect(page).toContain('route.query.process');
    expect(page).toContain("applyRoutedOrderTarget");
    expect(page).toContain(':auto-process=');
    expect(row).toContain("autoProcess: Boolean");
    expect(row).toContain("menuOpen.value = true");
  });

  it("uses one page-level server-anchored countdown clock", () => {
    expect(page).toContain("createOrderCountdownClock");
    expect(page).toContain("countdownClock.setServerTime(orderData.server_time)");
    expect(page).toContain('addEventListener("visibilitychange"');
    expect(page).toContain("startOrderCountdownClock");
    expect(page).toContain("stopOrderCountdownClock");
    expect(page).toContain(':now-seconds="nowSeconds"');
    expect((countdownClock.match(/setIntervalFn\(/g) || []).length).toBe(1);
  });

  it("uses a keyboard-accessible Chinese order context menu", () => {
    expect(page).toContain('@contextmenu.prevent.stop="openOrderContextMenu($event, order)"');
    expect(page).toContain('role="menu"');
    expect(page).toContain('aria-label="订单批量操作菜单"');
    expect(page).toContain("positionOrderContextMenu");
    expect(page).toContain("handleOrderContextMenuKeydown");
    expect(page).toContain("handleOrderContextPointerDown");
    expect(page).toContain("event.key === \"Escape\"");
    for (const label of [
      "选择当前项",
      "全选当前页",
      "全选筛选结果",
      "反选",
      "取消选择",
      "刷新所选订单",
      "打开待处理",
      "复制订单号",
      "批量标记",
      "批量取消标记",
    ]) {
      expect(page).toContain(label);
    }
    expect(page).toContain('call("orders.refresh-one"');
    expect(page).toContain('from "../clipboard"');
    expect(page).toContain("selected.map((order) => order.order_id).join(\"\\n\")");
    expect(page).toContain('call("orders.mark"');
    expect(page).toContain("batchMarking");
    expect(page).toContain("process: \"1\"");
  });
});

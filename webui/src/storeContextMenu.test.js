import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const page = readFileSync(
  new URL("./pages/StoresPage.vue", import.meta.url),
  "utf8",
);
const desktop = readFileSync(
  new URL("../../weimi_manager/webui/desktop.py", import.meta.url),
  "utf8",
);
const salesValue = readFileSync(
  new URL("./components/StoreSalesValue.vue", import.meta.url),
  "utf8",
);

describe("store context menu contract", () => {
  it("uses a custom Chinese menu and suppresses browser context menus", () => {
    expect(page).toContain('@contextmenu.prevent.stop="openStoreContextMenu($event, store)"');
    expect(page).toContain('role="menu"');
    expect(page).toContain("已选 {{ contextMenu.storeIds.length }} 家店铺");
    expect(page).toContain("批量设置分组…");
    expect(page).toContain("新建分组…");
    expect(page).toContain("批量删除所选…");
    expect(page).toContain("选择当前项");
    expect(page).toContain("全选当前页");
    expect(page).toContain("全选筛选结果");
    expect(page).toContain("反选");
    expect(page).toContain("取消选择");
    expect(page).toContain("刷新选中项");
    expect(page).toContain("复制选中项编号");
    expect(page).toContain("批量登录");
    expect(page).toContain("打开后台");
    expect(page).toContain("event.key === \"Escape\"");
    expect(page).toContain("pointerdown");
    expect(desktop).toContain(
      "view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)",
    );
  });

  it("provides accessible batch entry and real backend calls", () => {
    expect(page).toContain('class="btn muted store-batch-trigger"');
    expect(page).toContain(':aria-label="`批量操作，已选 ${selectedStoreIds.length} 家店铺`"');
    expect(page).toContain('call("stores.batch-assign-group"');
    expect(page).toContain('call("stores.batch-delete"');
    expect(page).toContain("store_ids: batchGroupDialog.storeIds");
    expect(page).toContain("store_ids: ids");
    expect(page).toContain("不会删除微信官方店铺");
    expect(page).toContain("selectedStoreIds.value = selectedStoreIds.value.filter");
    expect(page).toContain("expandedStoreIds.value = expandedStoreIds.value.filter");
  });

  it("shows metric provenance without exposing refund audit payloads", () => {
    expect(page).toContain(
      '<StoreSalesValue :display="salesDisplayFor(store, key)" />',
    );
    expect(salesValue).toContain(':title="display.title"');
    expect(page).not.toContain("退款单量来源");
    expect(page).not.toContain("订单状态200覆盖证据");
  });
});

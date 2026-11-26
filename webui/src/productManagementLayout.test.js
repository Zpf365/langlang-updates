import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  new URL("./pages/ProductsPage.vue", import.meta.url),
  "utf8",
);

describe("product management layout and recycle delete UI", () => {
  it("keeps the operation log expanded by default with a persisted collapse state", () => {
    expect(source).toContain('const OPERATION_LOG_EXPANDED_KEY = "langlang.products.operation-log-expanded"');
    expect(source).toContain("const operationLogExpanded = ref(storedOperationLogExpanded());");
    expect(source).toContain("return true;");
    expect(source).toContain("'logs-collapsed': !operationLogExpanded");
    expect(source).toContain("localStorage.setItem(OPERATION_LOG_EXPANDED_KEY");
    expect(source).toContain("transition:");
    expect(source).toContain("grid-template-columns 210ms ease");
  });

  it("provides accessible collapse and expand controls", () => {
    expect(source).toContain('title="缩小操作日志面板"');
    expect(source).toContain('aria-label="缩小操作日志面板"');
    expect(source).toContain('title="展开操作日志面板"');
    expect(source).toContain('aria-label="展开操作日志面板"');
    expect(source).toContain('class="operation-log-expand"');
    expect(source).toContain("v-if=\"!operationLogExpanded\"");
  });

  it("uses explicit recycle trash semantics without replacing quick local refresh", () => {
    expect(source).toContain('Number(product.status) === 6 ? "彻底删除" : "删除"');
    expect(source).toContain("allSelectedInRecycle.value");
    expect(source).toContain('"批量彻底删除"');
    expect(source).toContain("彻底删除操作不可恢复");
    expect(source).toContain("平台仍存在时不会显示成功");
    expect(source).toContain('call("products.delete"');
    expect(source).toContain("store_id: row.store_id");
    expect(source).toContain("await loadProducts()");
    expect(source).toContain("await loadOperationLogs()");
  });
});

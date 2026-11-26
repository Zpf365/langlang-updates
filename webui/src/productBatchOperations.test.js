import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  new URL("./pages/ProductsPage.vue", import.meta.url),
  "utf8",
);

describe("product batch operations", () => {
  it("runs per-product operations with visible progress and duplicate guards", () => {
    expect(source).toContain('call("products.set-listing"');
    expect(source).toContain('call("products.delete"');
    expect(source).toContain("if (operationBusy.value) return");
    expect(source).toContain("actionProgress.current");
    expect(source).toContain("actionProgress.total");
    expect(source).toContain("operation-progress-track");
    expect(source).toContain("下架中 ${actionProgress.current}/${actionProgress.total}");
    expect(source).toContain('actionProgress.type === "delete" && operationBusy');
    expect(source).toContain(":disabled=\"!selectedIds.length || operationBusy\"");
    expect(source).toContain("tab_keys: tabKeys");
    expect(source).toContain("Number(result.failed || 0) > 0");
    expect(source).toContain("result.errors?.[0]");
    expect(source).toContain("failed: status === 3");
    expect(source).toContain("strategy: status === 7");
    expect(source).not.toContain("window.confirm");
  });

  it("loads persistent product audit logs with result filters", () => {
    expect(source).toContain('call("products.operation-logs"');
    expect(source).toContain("operationLogFilter");
    expect(source).toContain("productOperationStatusLabel");
    expect(source).toContain("await loadOperationLogs()");
    expect(source).toContain("失败或待核实 ${failures.length}");
    expect(source).toContain('${listed ? "批量上架" : "批量下架"}完成：成功');
  });

  it("guards cancel-audit and official-backend actions while they are running", () => {
    expect(source).toContain("async function performCancelAudit()");
    expect(source).toContain("if (operationBusy.value) return");
    expect(source).toContain("actionProgress.type = \"cancel_audit\"");
    expect(source).toContain("撤回审核中 ${actionProgress.current}/${actionProgress.total}");
    expect(source).toContain(
      ':disabled="!selectedIds.length || operationBusy" @click="setCancelAudit()"',
    );
    expect(source).toContain("if (actionProgress.busy) return");
    expect(source).toContain("actionProgress.type = \"open_official\"");
  });
});

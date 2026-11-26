import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const currentDir = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(
  resolve(currentDir, "pages/LogisticsPage.vue"),
  "utf8",
);

describe("logistics abnormal-only page", () => {
  it("uses the real logistics RPC and refreshes through order sync", () => {
    expect(source).toContain('call("logistics.monitor"');
    expect(source).toContain('call("orders.sync"');
    expect(source).toContain("refreshLogistics");
  });

  it("lists only abnormal categories and keeps transit categories numeric", () => {
    expect(source).toContain('label: "已退回"');
    expect(source).toContain('label: "停滞"');
    expect(source).toContain('label: "售后"');
    expect(source).toContain("在途、待揽收、运输中只做数量统计，不列出来");
    expect(source).toContain('v-for="row in data.rows"');
  });

  it("supports store isolation, sorting and pagination", () => {
    expect(source).toContain('v-model="storeId"');
    expect(source).toContain('v-model="sort"');
    expect(source).toContain("changePage(page - 1)");
    expect(source).toContain("changePage(page + 1)");
  });

  it("makes every metric card a keyboard-accessible filter button", () => {
    expect(source).toContain('v-for="card in metricCards"');
    expect(source).toContain(':aria-pressed="selectedMetric === card.key"');
    expect(source).toContain('@click="selectMetric(card.key)"');
    expect(source).toContain('metric_filter: selectedMetric.value');
    expect(source).toContain("countOnlyFilters.has(selectedMetric.value)");
  });

  it("supports page size, page jump and opening the selected order", () => {
    expect(source).toContain("applyPageSize");
    expect(source).toContain("jumpToPage");
    expect(source).toContain("pageSizeInput");
    expect(source).toContain("jumpPageInput");
    expect(source).toContain('path: "/orders"');
    expect(source).toContain('process: "1"');
    expect(source).toContain('@click="openOrder(row)"');
    expect(source).toContain(">去处理</button>");
  });
});

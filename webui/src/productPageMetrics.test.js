import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { productMetricText } from "./productMetrics";
import {
  DEFAULT_PRODUCT_PAGE_SIZE,
  parseProductPageSize,
  productPageCount,
} from "./productPaging";

const source = readFileSync(
  new URL("./pages/ProductsPage.vue", import.meta.url),
  "utf8",
);

describe("product page metrics and pagination", () => {
  it("accepts positive custom page sizes and rejects blank or invalid values", () => {
    expect(DEFAULT_PRODUCT_PAGE_SIZE).toBe(50);
    for (const value of [1, 10, 50, 100, 500, 1000, 10000]) {
      expect(parseProductPageSize(value, 50)).toEqual({ value, error: "" });
    }
    expect(parseProductPageSize("", 50)).toEqual({
      value: 50,
      error: "每页数量不能为空",
    });
    expect(parseProductPageSize("0", 50).value).toBe(50);
    expect(parseProductPageSize("-1", 50).value).toBe(50);
    expect(parseProductPageSize("2.5", 50).value).toBe(50);
    expect(parseProductPageSize("10001", 50).value).toBe(50);
    expect(productPageCount(1001, 100)).toBe(11);
  });

  it("wires page size changes back to page one without blank crashes", () => {
    expect(source).toContain('v-model="perPageInput"');
    expect(source).toContain('@change="applyPerPage"');
    expect(source).toContain('@keyup.enter="applyPerPage"');
    expect(source).toContain("perPage.value = parsed.value;");
    expect(source).toContain("currentPage.value = 1;");
    expect(source).toContain("每页显示");
  });

  it("defaults to the first concrete store and offers explicit all-stores mode", () => {
    expect(source).toContain('<option :value="ALL_STORES">全部店铺</option>');
    expect(source).not.toContain("请选择微信小店");
    expect(source).toContain("filters.store_id = stores.value[0]?.id ?? ALL_STORES;");
    expect(source).toContain(":disabled=\"!filters.store_id || isAllStores || loading\"");
    expect(source).toContain("selectedStoreLabel }} · 页签");
  });

  it("renders the grouped 30-day metrics and total/review columns", () => {
    expect(source.match(/<col style="width: \d+px" \/>/g)).toHaveLength(12);
    expect(source).toContain('<th colspan="3" class="business-metric-head">近30天经营概况</th>');
    expect(source).toContain("30日销量 <span>");
    expect(source).toContain("30日曝光 <span>");
    expect(source).toContain("平台推荐 <span>");
    expect(source).toContain("总销量 <span>");
    expect(source).toContain("评价 <span>");
    expect(source).toContain("product.sold30");
    expect(source).toContain("product.views30");
    expect(source).toContain("product.recommend_views30");
    expect(source).toContain("product.total_sales");
    expect(source).toContain("product.review_count");
    expect(source).toContain("table-layout: fixed;");
    expect(source.match(/class="sort-button"/g)).toHaveLength(10);
    expect(source).toContain("sortIndicator(\"title\")");
    expect(source).toContain("sortIndicator(\"recommend_views30\")");
    expect(source).toContain("currentPage.value = 1;");
  });

  it("renders every missing metric as one short dash", () => {
    expect(productMetricText(null)).toBe("—");
    expect(productMetricText(undefined)).toBe("—");
    expect(productMetricText("")).toBe("—");
    expect(productMetricText(0)).toBe("0");
    expect(productMetricText(1234)).toBe("1,234");
    expect(source).not.toContain("好评率");
    expect(source).not.toContain("售后数");
  });
});

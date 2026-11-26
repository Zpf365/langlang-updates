import { describe, expect, it } from "vitest";

import {
  nextProductSort,
  sortProducts,
} from "./productSorting";

const rows = [
  { product_id: "商品10", title: "商品10", min_price: 0, stock_total: null },
  { product_id: "商品2", title: "商品2", min_price: -1, stock_total: 20 },
  { product_id: "商品1", title: "商品1", min_price: 10, stock_total: 0 },
  { product_id: "商品20", title: "商品20", min_price: 10, stock_total: 20 },
];

describe("product sorting", () => {
  it("cycles from unsorted to ascending, descending, then unsorted", () => {
    let state = { key: "", direction: "" };
    state = nextProductSort(state, "min_price");
    expect(state).toEqual({ key: "min_price", direction: "asc" });
    state = nextProductSort(state, "min_price");
    expect(state).toEqual({ key: "min_price", direction: "desc" });
    state = nextProductSort(state, "min_price");
    expect(state).toEqual({ key: "", direction: "" });
  });

  it("sorts numbers naturally and keeps null values last in both directions", () => {
    expect(
      sortProducts(rows, { key: "stock_total", direction: "asc" }).map(
        (row) => row.stock_total,
      ),
    ).toEqual([0, 20, 20, null]);
    expect(
      sortProducts(rows, { key: "stock_total", direction: "desc" }).map(
        (row) => row.stock_total,
      ),
    ).toEqual([20, 20, 0, null]);
    expect(
      sortProducts(rows, { key: "min_price", direction: "asc" }).map(
        (row) => row.min_price,
      ),
    ).toEqual([-1, 0, 10, 10]);
  });

  it("uses zh-CN natural ordering for Chinese and long numeric strings", () => {
    expect(
      sortProducts(rows, { key: "title", direction: "asc" }).map(
        (row) => row.title,
      ),
    ).toEqual(["商品1", "商品2", "商品10", "商品20"]);

    const longIds = [
      { product_id: "10000000000000000010" },
      { product_id: "1000000000000000002" },
      { product_id: "1000000000000000001" },
    ];
    expect(
      sortProducts(longIds, { key: "product_id", direction: "asc" }).map(
        (row) => row.product_id,
      ),
    ).toEqual([
      "1000000000000000001",
      "1000000000000000002",
      "10000000000000000010",
    ]);
  });

  it("sorts before pagination and therefore changes the first page", () => {
    const sorted = sortProducts(rows, { key: "min_price", direction: "desc" });
    expect(sorted.slice(0, 2).map((row) => row.product_id)).toEqual([
      "商品1",
      "商品20",
    ]);
  });
});

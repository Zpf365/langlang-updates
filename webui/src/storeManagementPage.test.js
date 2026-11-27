import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import {
  depositPresentation,
  scorePresentation,
} from "./storeScores";
import { APP_ID_REVEAL_TIMEOUT_MS, maskAppId } from "./storeIdentity";
import {
  exposurePresentation,
  fundPresentation,
  isRefundVisible,
  newProductsTodayPresentation,
  onlineProductsPresentation,
  salesPresentation,
  shanghaiDateKey,
} from "./storeMetrics";

const source = readFileSync(
  new URL("./pages/StoresPage.vue", import.meta.url),
  "utf8",
);

describe("store management score and deposit presentation", () => {
  it("removes only the three unverified columns and retains deposit", () => {
    expect(source).not.toContain("信誉分");
    expect(source).not.toContain("经营类目");
    expect(source).not.toContain("经营分");
    expect(source).toContain('<th rowspan="2">保证金</th>');
    expect(source).toContain("depositPresentation(store)");
    expect(source).toContain("const STORE_TABLE_COLUMN_COUNT = 23;");
    expect(source).toContain("const STORE_OVERVIEW_COLUMN_COUNT = 12;");
    expect(source).toContain("result.cash = completeSum(");
    expect(source).toContain("result.settle = completeSum(");
    expect(source).not.toContain("请打开资金账户刷新");
    expect(source).toContain(':colspan="STORE_TABLE_COLUMN_COUNT"');
    expect(source.match(/<col style="width: \d+px" \/>/g)).toHaveLength(33);
    expect(source).toContain('<col :style="{ width: `${appIdColumnWidth}px` }" />');
    expect(source).toContain('<col :style="{ width: `${secretColumnWidth}px` }" />');
    expect(source).toContain("table-layout: fixed;");
    expect(source).toContain("width: 2538px;");
    expect(source).toContain("2538");
  });

  it("masks AppID by default and auto-collapses after 60 seconds", () => {
    expect(maskAppId("wx3abcdef1234567")).toBe("wx3a…");
    expect(maskAppId("")).toBe("—");
    expect(APP_ID_REVEAL_TIMEOUT_MS).toBe(60000);
    expect(source).toContain("revealedAppIds[store.id] ? store.app_id : maskAppId(store.app_id)");
    expect(source).toContain('@click="toggleAppId(store)"');
    expect(source).toContain("隐藏完整 AppID");
    expect(source).toContain("显示完整 AppID");
    expect(source).not.toContain('class="app-id-cell" :title="store.app_id"');
    expect(source).toContain("APP_ID_REVEAL_TIMEOUT_MS");
  });

  it("renders grouped exposure, online products and today new observation", () => {
    expect(source).toContain('<th colspan="3" class="exposure-group-head">商品曝光人数</th>');
    expect(source).toContain("<th class=\"count-cell\">今日</th>");
    expect(source).toContain("<th class=\"count-cell\">昨日</th>");
    expect(source).toContain("<th class=\"count-cell\">7日</th>");
    expect(source).toContain("<th rowspan=\"2\">在线商品数</th>");
    expect(source).toContain("<th rowspan=\"2\">今日上新</th>");
    expect(source).toContain("function toggleAppId(store)");
    expect(source).toContain("function hideAppId(storeId)");

    const exposure = exposurePresentation(
      {
        exposure_metrics: { today: 16754, yesterday: 0, seven_days: 125600 },
        exposure_counts: { today: 21084, yesterday: 0, seven_days: 150000 },
        exposure_people: { today: 16754, yesterday: 0, seven_days: 125600 },
        product_exposure_counts: {
          today: 21084,
          yesterday: 0,
          seven_days: 150000,
        },
        product_exposure_people: {
          today: 16754,
          yesterday: 0,
          seven_days: 125600,
        },
        exposure_click_people: { today: 321, yesterday: 0, seven_days: 2400 },
        exposure_click_counts: { today: 500, yesterday: 0, seven_days: 3800 },
        exposure_meta: {
          status: { today: "updated" },
          count_status: { today: "updated" },
          people_status: { today: "updated" },
          updated_at: "2026-11-22 10:00:00",
          source: "官方曝光区间接口",
        },
      },
      "today",
    );
    const online = onlineProductsPresentation({
      online_products: 5678,
      online_products_meta: { source: "官方在售总数" },
    });
    const newToday = newProductsTodayPresentation({
      new_products_today: 0,
      new_products_today_meta: { scan_completed_today: true },
    });
    const unseen = newProductsTodayPresentation({ new_products_today: null });
    const stalePreviousDay = newProductsTodayPresentation({
      new_products_today: 30,
      new_products_today_meta: {
        scan_completed_today: false,
        last_scan_completed_at: "2026-11-26T23:58:00+08:00",
      },
    });

    expect(exposure.text).toBe("16,754");
    expect(exposure.title).toContain("今日商品曝光人数 16,754");
    expect(exposure.title).toContain("商品曝光次数 21,084");
    expect(exposure.title).toContain("商品点击人数 321");
    expect(exposure.title).toContain("商品点击次数 500");
    expect(exposure.title).toContain("官方曝光区间接口");
    expect(online.text).toBe("5,678");
    expect(newToday.text).toBe("0");
    expect(newToday.title).toContain("不代表平台商品创建时间");
    expect(unseen.text).toBe("—");
    expect(stalePreviousDay.text).toBe("—");
    expect(stalePreviousDay.title).toContain("今日尚未完成商品目录扫描");
  });

  it("does not turn missing exposure or online counts into zero", () => {
    expect(exposurePresentation({}, "today").text).toBe("—");
    expect(exposurePresentation({
      product_exposure_counts: { today: 21 },
      product_exposure_people: { today: null },
    }, "today").text).toBe("—");
    expect(exposurePresentation({
      product_exposure_counts: { today: null },
      product_exposure_people: { today: 17 },
    }, "today").text).toBe("17");
    expect(exposurePresentation({
      product_exposure_counts: { today: 0 },
      product_exposure_people: { today: 0 },
    }, "today").text).toBe("0");
    expect(onlineProductsPresentation({
      online_products: null,
      online_products_meta: { source: "" },
    }).text).toBe("—");
  });

  it("never publishes recommendation traffic as product exposure", () => {
    const legacyOnly = exposurePresentation({
      exposure_people: { today: 999 },
      exposure_metrics: { today: 888 },
      recommend_exposure_people: 777,
      exposure_meta: {
        status: { today: "updated" },
        as_of_date: "2026-11-27",
      },
    }, "today", { today: "2026-11-27" });
    const compass = exposurePresentation({
      product_exposure_people: { today: 17 },
      product_exposure_counts: { today: 21 },
      exposure_people: { today: 999 },
      exposure_meta: {
        status: { today: "updated" },
        as_of_date: "2026-11-27",
      },
    }, "today", { today: "2026-11-27" });

    expect(legacyOnly.text).toBe("—");
    expect(compass.text).toBe("17");
    expect(compass.title).toContain("商品曝光次数 21");
  });

  it("hides cached store values after the Shanghai business date changes", () => {
    expect(shanghaiDateKey("2026-11-26T16:00:00Z")).toBe("2026-11-27");
    const stale = {
      as_of_date: "2026-11-26",
      metrics: {
        gmv_today: 100,
        today_orders: 1,
        refund_gmv_today: 0,
      },
      sales_meta: {
        as_of_date: "2026-11-26",
        refund_order_counts: { today: 0 },
      },
      exposure_people: { today: 30, yesterday: 40, seven_days: 50 },
      exposure_meta: { as_of_date: "2026-11-26" },
      new_products_today: 30,
      new_products_today_meta: {
        scan_completed_today: true,
        as_of_date: "2026-11-26",
      },
    };
    const sales = salesPresentation(stale, "today", {
      today: "2026-11-27",
    });
    const exposure = exposurePresentation(stale, "today", {
      today: "2026-11-27",
    });
    const newProducts = newProductsTodayPresentation(stale, {
      today: "2026-11-27",
    });

    expect(sales.stale).toBe(true);
    expect(sales.salesText).toBe("销 — · —单");
    expect(sales.showRefund).toBe(false);
    expect(exposure.stale).toBe(true);
    expect(exposure.text).toBe("—");
    expect(newProducts.text).toBe("—");
    expect(newProducts.title).toContain("今日尚未完成商品目录扫描");
  });

  it("places credentials after deposit and freezes the three identity columns", () => {
    expect(source.indexOf("<th rowspan=\"2\">AppID</th>")).toBeGreaterThan(
      source.indexOf("<th rowspan=\"2\">保证金</th>"),
    );
    expect(source.indexOf("<th rowspan=\"2\">AppSecret</th>")).toBeGreaterThan(
      source.indexOf("<th rowspan=\"2\">AppID</th>"),
    );
    expect(source).toContain("sticky-select sticky-column");
    expect(source).toContain("sticky-index sticky-column");
    expect(source).toContain("sticky-name sticky-column");
    expect(source).toContain("left: 44px;");
    expect(source).toContain("left: 98px;");
    const fullLogin = source.indexOf('<th rowspan="2">登录状态</th>');
    const fullOperation = source.lastIndexOf('<th rowspan="2">操作</th>');
    expect(fullLogin).toBeGreaterThan(
      source.indexOf('<th rowspan="2">AppSecret</th>'),
    );
    expect(fullLogin).toBeLessThan(fullOperation);
  });

  it("defaults to a no-horizontal-scroll overview with expandable details", () => {
    expect(source).toContain('const storeViewMode = ref(STORE_VIEW_OVERVIEW)');
    expect(source).toContain('v-if="storeViewMode === STORE_VIEW_OVERVIEW"');
    expect(source).toContain('v-else class="card data-card store-table-card"');
    expect(source).toContain('class="table-scroll overview-table-scroll"');
    expect(source).toContain("overflow-x: hidden;");
    expect(source).toContain("销售 / 退款（金额 / 单量）");
    expect(source).toContain(':aria-expanded="isStoreExpanded(store.id)"');
    expect(source).toContain('class="store-detail-grid"');
    expect(source).toContain("商品曝光人数");
    expect(source).toContain("资金与保证金");
    expect(source).toContain("体验分");
    expect(source).toContain("result[`${summaryKey}_orders`]");
    expect(source).toContain("result[`refund_${summaryKey}`]");
    expect(source).toContain("summaryCount(summary.refund_today_orders)");
    expect(source).toContain('v-if="salesPresentation(store, key).showRefund"');
    expect(source).toContain("class=\"sales-lines\"");
    expect(source).toContain("isRefundVisible(summary.refund_today");
    expect(source).toContain("store.web_status_detail || store.web_status");
  });

  it("enlarges every store table text tier within the two table views", () => {
    expect(source).toContain("--store-table-heading-font-size: 11px;");
    expect(source).toContain("--store-table-data-font-size: 11px;");
    expect(source).toContain("--store-table-secondary-font-size: 10px;");
    expect(source).toContain("--store-table-sales-font-size: 12px;");
    expect(source).toContain("--store-table-heading-font-size: 13px;");
    expect(source).toContain("--store-table-data-font-size: 13px;");
    expect(source).toContain("--store-table-secondary-font-size: 12px;");
    expect(source).toContain("--store-table-sales-font-size: 13px;");
    expect(source).toContain(
      "font-size: var(--store-table-sales-font-size);",
    );
    expect(source).toContain(
      "font-size: var(--store-table-secondary-font-size);",
    );
  });

  it("shows refund rows only for real refund data or unknown values", () => {
    const noRefund = salesPresentation(
      {
        metrics: {
          gmv_today: 5300,
          today_orders: 1,
          refund_gmv_today: 0,
        },
        sales_meta: { refund_order_counts: { today: 0 } },
      },
      "today",
    );
    const bothZero = salesPresentation(
      {
        metrics: {
          gmv_yesterday: 0,
          orders_yesterday: 0,
          refund_gmv_yesterday: 0,
        },
        sales_meta: { refund_order_counts: { yesterday: 0 } },
      },
      "yesterday",
    );
    const refundOnly = salesPresentation(
      {
        metrics: {
          gmv_7d: 0,
          orders_7d: 0,
          refund_gmv_7d: 1200,
        },
        sales_meta: { refund_order_counts: { seven_days: 1 } },
      },
      "seven_days",
    );
    const amountOnly = salesPresentation(
      {
        metrics: {
          gmv_30d: 8800,
          orders_30d: 2,
          refund_gmv_30d: 500,
        },
        sales_meta: { refund_order_counts: { thirty_days: 0 } },
      },
      "thirty_days",
    );
    const countOnly = salesPresentation(
      {
        metrics: {
          gmv_today: 9900,
          today_orders: 3,
          refund_gmv_today: 0,
        },
        sales_meta: { refund_order_counts: { today: 75 } },
      },
      "today",
    );
    const unknownAmount = salesPresentation(
      {
        metrics: { gmv_today: 100, today_orders: 1 },
        sales_meta: { refund_order_counts: { today: 0 } },
      },
      "today",
    );
    const unknownCount = salesPresentation(
      {
        metrics: {
          gmv_today: 100,
          today_orders: 1,
          refund_gmv_today: 0,
        },
        sales_meta: { refund_order_counts: {} },
      },
      "today",
    );

    expect(noRefund.salesText).toBe("销 ¥53.00 · 1单");
    expect(noRefund.showRefund).toBe(false);
    expect(bothZero.salesText).toBe("销 ¥0.00 · 0单");
    expect(bothZero.showRefund).toBe(false);
    expect(refundOnly.showRefund).toBe(true);
    expect(refundOnly.salesText).toBe("销 ¥0.00 · 0单");
    expect(refundOnly.refundText).toBe("退 ¥12.00 · 1单");
    expect(amountOnly.showRefund).toBe(true);
    expect(amountOnly.refundText).toBe("退 ¥5.00 · —单");
    expect(countOnly.showRefund).toBe(false);
    expect(countOnly.refundText).toBe("退 ¥0.00 · —单");
    expect(countOnly.refundOrderCount).toBeNull();
    expect(countOnly.title).toBeUndefined();
    expect(unknownAmount.showRefund).toBe(false);
    expect(unknownAmount.refundText).toBe("退 — · 0单");
    expect(unknownCount.showRefund).toBe(false);
    expect(unknownCount.refundText).toBe("退 ¥0.00 · —单");
    expect(isRefundVisible(0, 0)).toBe(false);
    expect(isRefundVisible(null, 0)).toBe(false);
    expect(isRefundVisible(0, null)).toBe(false);
    expect(isRefundVisible(undefined, undefined)).toBe(true);
  });

  it("hides an explicit zero refund even when a stale count says one", () => {
    const zeroRefund = salesPresentation(
      {
        metrics: {
          gmv_today: 0,
          today_orders: 0,
          refund_gmv_today: 0,
        },
        sales_meta: {
          refund_order_counts: { today: 1 },
        },
      },
      "today",
    );
    const realRefund = salesPresentation(
      {
        metrics: {
          gmv_today: 0,
          today_orders: 0,
          refund_gmv_today: 100,
        },
        sales_meta: {
          refund_order_counts: { today: 1 },
        },
      },
      "today",
    );

    expect(zeroRefund.salesText).toBe("销 ¥0.00 · 0单");
    expect(zeroRefund.showRefund).toBe(false);
    expect(realRefund.showRefund).toBe(true);
    expect(realRefund.refundText).toBe("退 ¥1.00 · 1单");
  });

  it("keeps refund visibility scoped to each store period after refresh", () => {
    const first = {
      id: 1,
      metrics: {
        gmv_today: 100,
        today_orders: 1,
        refund_gmv_today: 0,
        gmv_yesterday: 200,
        orders_yesterday: 2,
        refund_gmv_yesterday: 50,
      },
      sales_meta: {
        refund_order_counts: { today: 0, yesterday: 1 },
      },
    };
    const second = {
      id: 2,
      metrics: {
        gmv_today: 0,
        today_orders: 0,
        refund_gmv_today: 0,
      },
      sales_meta: { refund_order_counts: { today: 0 } },
    };

    expect(salesPresentation(first, "today").showRefund).toBe(false);
    expect(salesPresentation(first, "yesterday").showRefund).toBe(true);
    expect(salesPresentation(second, "today").showRefund).toBe(false);

    first.metrics.refund_gmv_today = 100;
    first.sales_meta.refund_order_counts.today = 1;
    expect(salesPresentation(first, "today").showRefund).toBe(true);
    expect(salesPresentation(first, "today").refundText).toBe("退 ¥1.00 · 1单");
  });

  it("renders official deal values without diagnostic tooltip payloads", () => {
    const fresh = salesPresentation(
      {
        metrics: { gmv_today: 13458 },
        sales_meta: {
          status: { today: "updated" },
          sources: { today: "微信小店实时成交统计" },
          updated_at: "2026-11-22 17:50:41",
        },
      },
      "today",
    );
    const stale = salesPresentation(
      {
        metrics: { gmv_yesterday: 0 },
        sales_meta: {
          status: { yesterday: "failed" },
          source: "本地订单缓存降级",
          updated_at: "2026-11-22 17:30:00",
          error: "timeout",
        },
      },
      "yesterday",
    );
    const missing = salesPresentation(
      { metrics: {}, sales_meta: { status: { seven_days: "missing" } } },
      "seven_days",
    );

    expect(fresh.text).toBe("¥134.58");
    expect(fresh.title).toBeUndefined();
    const giftAdjusted = salesPresentation(
      {
        metrics: {
          gmv_today: 949999,
          today_orders: 65,
          refund_gmv_today: 1200,
        },
        sales_meta: {
          status: { today: "updated" },
          sources: { today: "微信小店实时成交统计 - 未领取礼物" },
          refund_amounts: {
            status: { today: "updated" },
            sources: { today: "微信小店实时成交统计" },
          },
          refund_order_counts: { today: 2 },
          refund_order_cache: {
            status: "fresh",
            updated_at: "2026-11-23 11:30:00",
            sources: {
              today: [
                "官方售后完成时间（complete_time/update_time）",
              ],
            },
            covered: { today: true },
            conflict_periods: [],
            order_status_evidence: { today: 2 },
            coverage: {
              today: {
                start: "2026-11-23 00:00:00",
                end: "2026-11-23 12:00:00",
              },
            },
          },
          gift_exclusion: {
            today: {
              deducted_amount: 50000,
              deducted_orders: 1,
              pending_order_count: 1,
              unconfirmed_count: 1,
            },
          },
        },
      },
      "today",
    );
    expect(giftAdjusted.text).toBe("¥9,499.99");
    expect(giftAdjusted.salesText).toBe("销 ¥9,499.99 · 65单");
    expect(giftAdjusted.refundText).toBe("退 ¥12.00 · 2单");
    expect(giftAdjusted.title).toBeUndefined();
    const unknownRefundCount = salesPresentation(
      {
        metrics: {
          gmv_today: 100,
          today_orders: 1,
          refund_gmv_today: 50,
        },
        sales_meta: {
          refund_order_counts: { today: null },
          refund_order_cache: {
            status: "fresh",
            gated_periods: ["today"],
            hint: "官方退款金额大于 0，但本地售后明细覆盖不足，单量显示待同步",
          },
        },
      },
      "today",
    );
    expect(unknownRefundCount.refundText).toBe("退 ¥0.50 · —单");
    expect(unknownRefundCount.title).toBeUndefined();
    const missingRefundCount = salesPresentation(
      {
        metrics: { gmv_today: 100, today_orders: 1, refund_gmv_today: 0 },
        sales_meta: {
          refund_order_counts: {},
          refund_order_cache: {
            status: "missing",
            hint: "售后单量缓存待刷新",
          },
        },
      },
      "today",
    );
    expect(missingRefundCount.refundText).toBe("退 ¥0.00 · —单");
    expect(missingRefundCount.title).toBeUndefined();
    expect(stale.text).toBe("¥0.00");
    expect(stale.title).toContain("更新时间 2026-11-22 17:30:00");
    expect(stale.title).toContain("本次读取失败");
    expect(stale.title).toContain("错误 timeout");
    expect(missing.text).toBe("—");
  });

  it("keeps unknown fund balances unknown and exposes stale fund provenance", () => {
    const unknown = fundPresentation({
      metrics: { cash: null, settle: null },
      fund_meta: {
        status: { cash: "missing", settle: "missing" },
        source: "资金接口",
        as_of_date: "",
        error: "接口未返回余额",
      },
    }, "cash", { today: "2026-11-27" });
    const stale = fundPresentation({
      metrics: { cash: 12300, settle: 0 },
      fund_meta: {
        status: { cash: "stale", settle: "updated" },
        source: "资金账户旧缓存",
        as_of_date: "2026-11-26",
        updated_at: "2026-11-26 08:10:00",
        error: "刷新超时",
      },
    }, "cash", { today: "2026-11-27" });
    const realZero = fundPresentation({
      metrics: { settle: 0 },
      fund_meta: {
        status: { settle: "updated" },
        source: "资金账户",
        as_of_date: "2026-11-27",
      },
    }, "settle", { today: "2026-11-27" });

    expect(unknown.text).toBe("—");
    expect(unknown.title).toContain("接口未返回余额");
    expect(stale.text).toBe("¥123.00");
    expect(stale.stale).toBe(true);
    expect(stale.title).toContain("数据日期 2026-11-26");
    expect(stale.title).toContain("沿用最后可信值");
    expect(stale.title).toContain("错误 刷新超时");
    expect(realZero.text).toBe("¥0.00");
    expect(realZero.stale).toBe(false);
  });

  it("uses each exposure period date instead of the page date", () => {
    const exposure = exposurePresentation({
      exposure_people: { today: 30 },
      exposure_meta: {
        as_of_date: "2026-11-27",
        period_dates: { today: "2026-11-25" },
        status: { today: "stale" },
        source: "商品数据缓存",
        error: "Compass 超时",
      },
    }, "today", { today: "2026-11-27" });

    expect(exposure.text).toBe("—");
    expect(exposure.title).toContain("数据日期 2026-11-25");
    expect(exposure.title).toContain("Compass 超时");
  });

  it("renders value, amplitude, tooltip and missing status for each score", () => {
    const store = {
      score_state: {
        values: {
          dsr: {
            value: 4.73,
            updated_at: "2026-11-22 10:00:00",
            source: "微信小店统计接口 体验分",
          },
        },
      },
      score_changes: {
        dsr: {
          from: 4.65,
          to: 4.73,
          delta: 0.08,
          updated_at: "2026-11-22 10:00:00",
          baseline_date: "2026-11-21",
          comparison_date: "2026-11-22",
        },
      },
      score_refresh: {
        dsr: { status: "missing", error: "" },
      },
    };

    const presentation = scorePresentation(store, "dsr");

    expect(presentation.valueText).toBe("4.73");
    expect(presentation.changeText).toBe("↑");
    expect(presentation.changeTitle).toContain("较昨日");
    expect(presentation.changeTitle).toContain("4.65 → 4.73");
    expect(presentation.changeTitle).toContain(
      "官方日 2026-11-21 → 2026-11-22",
    );
    expect(presentation.statusLabel).toBe("");
    expect(presentation.valueTitle).toContain("2026-11-22 10:00:00");
    expect(source).toContain(
      ':title="scorePresentation(store, key).changeTitle"',
    );
  });

  it("renders arrow-only direction without inventing an unknown trend", () => {
    const down = scorePresentation(
      {
        score_state: { values: { score_deliver: { value: 4.53 } } },
        score_changes: {
          score_deliver: {
            from: 4.60,
            to: 4.53,
            delta: -0.07,
            updated_at: "2026-11-22 10:00:00",
          },
        },
        score_refresh: { score_deliver: { status: "updated" } },
      },
      "score_deliver",
    );
    const unchanged = scorePresentation(
      {
        score_state: { values: { dsr: { value: 4.73 } } },
        score_changes: {},
        score_refresh: { dsr: { status: "updated" } },
      },
      "dsr",
    );

    expect(down.changeText).toBe("↓");
    expect(down.changeDirection).toBe("down");
    expect(unchanged.changeText).toBe("");
  });

  it("shows no arrow without a daily change record or for unknown scores", () => {
    const base = {
      score_state: { values: { dsr: { value: 4.73 } } },
      score_refresh: { dsr: { status: "updated" } },
    };
    const missingTime = scorePresentation(
      {
        ...base,
        score_changes: {
          dsr: { from: 4.65, to: 4.73, delta: 0.08 },
        },
      },
      "dsr",
    );
    const unknownValue = scorePresentation(
      {
        score_state: { values: { dsr: { value: null } } },
        score_changes: {
          dsr: {
            from: 4.65,
            to: 4.73,
            delta: 0.08,
            updated_at: "2026-11-22 10:00:00",
          },
        },
        score_refresh: { dsr: { status: "missing" } },
      },
      "dsr",
    );
    const noChange = scorePresentation(
      {
        ...base,
        score_changes: {},
      },
      "dsr",
    );

    expect(missingTime.changeText).toBe("↑");
    expect(missingTime.changeTitle).toContain("时间暂无");
    expect(noChange.changeText).toBe("");
    expect(unknownValue.changeText).toBe("");
    expect(source).toContain('v-for="key in STORE_SCORE_FIELDS" :key="key"');
  });

  it("never renders an unreliable legacy zero as real deposit", () => {
    const unknown = depositPresentation({
      metrics: { deposit: 0 },
      deposit_meta: {
        status: "unavailable",
        source: "",
        updated_at: "",
        error: "",
      },
    });
    const real = depositPresentation({
      metrics: { deposit: 12345 },
      deposit_meta: {
        status: "updated",
        source: "微信小店 getDepositMPayState",
        updated_at: "2026-11-22 10:00:00",
        error: "",
      },
    });
    const realZero = depositPresentation({
      metrics: { deposit: 0 },
      deposit_meta: {
        status: "updated",
        source: "微信小店 getDepositMPayState",
        updated_at: "2026-11-22 10:00:00",
        error: "",
      },
    });

    expect(unknown.valueText).toBe("—");
    expect(real.valueText).toBe("¥123.45");
    expect(realZero.valueText).toBe("¥0.00");
    expect(real.detail).toContain("getDepositMPayState");
  });
});

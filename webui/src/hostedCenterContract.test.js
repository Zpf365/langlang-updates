import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  new URL("./pages/HostedPage.vue", import.meta.url),
  "utf8",
);

describe("hosted center full-store commission flow", () => {
  it("centers the page on one all-store commission setting", () => {
    expect(source).toContain("全店联盟佣金");
    expect(source).toContain("一键设置佣金");
    expect(source).toContain("开通全部店铺佣金");
    expect(source).toContain("保存并立即开通");
    expect(source).toContain("全部在售商品");
    expect(source).toContain("每 20 个商品自动分成一批");
  });

  it("removes low-value hosted actions from the active console", () => {
    expect(source).not.toContain("商品上架");
    expect(source).not.toContain("限时抢购");
    expect(source).not.toContain("价格倍率");
    expect(source).not.toContain("达人邀约");
  });

  it("enables execution and retires hidden legacy actions on save", () => {
    expect(source).toContain('Object.hasOwn(source, "enable_league")');
    expect(source).toContain("enabled: Boolean(config.enable_league)");
    expect(source).toContain("enable_commission: Boolean(config.enable_league && config.enable_commission)");
    expect(source).toContain("product_commission_percent: ratio");
    expect(source).toContain("...RETIRED_ACTION_DEFAULTS");
    expect(source).toContain("storeConfigured(store)");
  });
});

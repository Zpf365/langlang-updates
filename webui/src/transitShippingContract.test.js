import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const page = readFileSync(
  new URL("./pages/TransitPage.vue", import.meta.url),
  "utf8",
);
const router = readFileSync(
  new URL("./router.js", import.meta.url),
  "utf8",
);

describe("transit shipping workspace contract", () => {
  it("restores the real workspace after the startup fix", () => {
    expect(router).toContain(
      'component: () => import("./pages/TransitPage.vue")',
    );
  });

  it("uses one real continuous apply RPC after overview", () => {
    expect(page).toContain('call("transit.shipping.overview"');
    expect(page).toContain('call("transit.shipping.apply"');
    expect(page).toContain("template_ids");
    expect(page).toContain("confirmed: true");
    expect(page.match(/call\("transit\.shipping\.apply"/g)).toHaveLength(1);
  });

  it("exposes editable direct, special and transit fees", () => {
    expect(page).toContain("form.direct_first_price");
    expect(page).toContain("form.direct_second_price");
    expect(page).toContain("form.special_first_price");
    expect(page).toContain("form.special_second_price");
    expect(page).toContain("form.transit_first_price");
    expect(page).toContain("form.transit_second_price");
    expect(page).toContain("新疆会从直邮指定地区和“不配送地区”移除");
  });

  it("has one explicit confirmation for the whole continuous flow", () => {
    expect(page).toContain("确认执行全店中转连续流程");
    expect(page).toContain("这是真实平台写操作");
    expect(page).toContain("确认并连续执行");
    expect(page).toContain("服务开通");
    expect(page).toContain("商品切换");
    expect(page).toContain("模板更新");
    expect(page).toContain("最终回读");
    expect(page).toContain("全店中转连续流程完成");
  });
});

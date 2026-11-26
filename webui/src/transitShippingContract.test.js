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

  it("uses real overview, save and apply RPCs", () => {
    expect(page).toContain('call("transit.shipping.overview"');
    expect(page).toContain('call("transit.shipping.rules.save"');
    expect(page).toContain('call("transit.shipping.apply"');
    expect(page).toContain("template_ids");
    expect(page).toContain("set_all_products_transit");
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

  it("has an explicit official write confirmation", () => {
    expect(page).toContain("确认修改官方运费模板");
    expect(page).toContain("这是真实平台写操作");
    expect(page).toContain("确认执行");
  });
});

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const page = readFileSync(
  new URL("./pages/ActivityCenterPage.vue", import.meta.url),
  "utf8",
);

describe("smart snapup automation UI", () => {
  it("shows one account-level switch and status strip", () => {
    expect(page).toContain("智能限时活动托管：");
    expect(page).toContain("上次扫描");
    expect(page).toContain("下次扫描");
    expect(page).toContain("待处理");
    expect(page).toContain("cloudStatusLabel()");
    expect(page).toContain('call("activity.renewal.save"');
  });

  it("saves immutable rule snapshots and shows automatic run records", () => {
    expect(page).toContain('call("activity.smart-auto.rules.save"');
    expect(page).toContain("保存为智能活动规则");
    expect(page).toContain("自动新商品活动记录");
    expect(page).toContain("规则版本");
    expect(page).toContain('call("activity.smart-auto.stop"');
  });

  it("does not create a page-level scheduler", () => {
    expect(page).not.toContain("setInterval");
    expect(page).not.toContain("ActivityAutoRenewalManager");
  });
});

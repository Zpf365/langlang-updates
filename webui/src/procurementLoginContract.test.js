import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const page = readFileSync(
  new URL("./pages/ProcurementPage.vue", import.meta.url),
  "utf8",
);

describe("procurement account login contract", () => {
  it("supports Taobao and 1688 accounts without requiring password entry", () => {
    expect(page).toContain("淘宝");
    expect(page).toContain("1688");
    expect(page).toContain('placeholder="留空表示不修改"');
    expect(page).toContain('call("procurement.create"');
  });

  it("opens the per-row official backend only for logged-in accounts", () => {
    expect(page).toContain('call("procurement.open-backend.start"');
    expect(page).toContain('call("procurement.open-backend.status"');
    expect(page).toContain("打开后台");
    expect(page).toContain('busyAction === \'backend\' ? "后台已打开..."');
    expect(page).toContain("account.status !== '已登录'");
    expect(page).toContain("请先扫码登录");
  });

  it("shows explicit scan, success and failure states", () => {
    expect(page).toContain("等待官方登录");
    expect(page).toContain("登录成功");
    expect(page).toContain("登录未完成");
    expect(page).toContain("登录窗口已自动关闭");
  });

  it("probes saved sessions at most once per software run", () => {
    expect(page).toContain("procurement-probed:${account.id}");
    expect(page).toContain("sessionStorage.getItem");
    expect(page).toContain("sessionStorage.setItem");
  });
});

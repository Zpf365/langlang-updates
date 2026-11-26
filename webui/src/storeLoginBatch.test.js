import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const page = readFileSync(
  new URL("./pages/StoresPage.vue", import.meta.url),
  "utf8",
);

describe("store batch login contract", () => {
  it("sends one batch RPC instead of opening one browser per store", () => {
    expect(page.match(/call\("stores\.login"/g)).toHaveLength(1);
    expect(page).toMatch(
      /call\("stores\.login",\s*\{\s*store_ids: storeIds,/s,
    );
    expect(page).toContain(
      "background_refresh_enabled: autoRefresh.enabled.value",
    );

    const branchStart = page.indexOf("if (isLogin) {");
    const genericLoop = page.indexOf("for (const [index, storeId] of storeIds.entries())");
    expect(branchStart).toBeGreaterThan(-1);
    expect(genericLoop).toBeGreaterThan(branchStart);
    const loginBranch = page.slice(branchStart, genericLoop);
    expect(loginBranch).not.toContain("for (const");
    expect(loginBranch).toContain("not_logged_in_store_ids");
    expect(loginBranch).toContain("missing_store_ids");
    expect(loginBranch).toContain("未再重复打开登录窗口");
  });

  it("keeps the operation busy lock and handles a backend busy response", () => {
    expect(page).toContain("if (storeActionBusy.value) return");
    expect(page).toContain("storeActionBusy.value = true");
    expect(page).toContain("if (result.busy)");
    expect(page).toContain("网页登录任务正在进行");
    expect(page).toContain("storeActionBusy.value = false");
  });
});

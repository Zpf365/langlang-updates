import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const app = readFileSync(new URL("./App.vue", import.meta.url), "utf8");
const session = readFileSync(new URL("./session.js", import.meta.url), "utf8");

describe("account switch contract", () => {
  it("uses real task-status and account-action RPCs with in-app confirmation", () => {
    expect(app).toContain('call("account.task-status"');
    expect(app).toContain('call("account.action"');
    expect(app).toContain("切换账号");
    expect(app).toContain("退出登录");
    expect(app).toContain("<ModalDialog");
    expect(app).not.toContain("window.confirm");
    expect(app).not.toContain("window.alert");
  });

  it("lets the user switch immediately and freezes old account results", () => {
    expect(app).toContain("beginAccountHandoff(accountAction.value)");
    expect(app).toContain("立即安全");
    expect(app).toContain("旧账号后台任务会暂停保留");
    expect(app).not.toContain("当前禁止切换");
    expect(app).not.toContain("accountTasks.length > 0");
    expect(app).toContain("resetSessionState()");
    expect(app).toContain("sessionStorage.clear()");
    expect(app).toContain("localStorage.clear()");
    expect(app).toContain("router.replace(\"/shops\")");
    expect(session).toContain("export function resetSessionState()");
    expect(session).toContain("sessionState.bootstrap = null");
  });

  it("cancels in-flight requests and rejects late results by generation", () => {
    const api = readFileSync(new URL("./api.js", import.meta.url), "utf8");
    const autoRefresh = readFileSync(
      new URL("./autoRefresh.js", import.meta.url),
      "utf8",
    );
    const storeSync = readFileSync(
      new URL("./storeListSync.js", import.meta.url),
      "utf8",
    );

    expect(api).toContain("beginAccountHandoff");
    expect(api).toContain("accountGeneration");
    expect(api).toContain("controller.abort()");
    expect(api).toContain("ACCOUNT_HANDOFF_STALE");
    expect(autoRefresh).toContain('"langlang-account-handoff"');
    expect(storeSync).toContain('"langlang-account-handoff"');
  });
});

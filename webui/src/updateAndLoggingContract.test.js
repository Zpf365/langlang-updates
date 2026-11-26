import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const app = readFileSync(new URL("./App.vue", import.meta.url), "utf8");
const more = readFileSync(
  new URL("./pages/MorePage.vue", import.meta.url),
  "utf8",
);
const api = readFileSync(new URL("./api.js", import.meta.url), "utf8");
const autoRefresh = readFileSync(
  new URL("./autoRefresh.js", import.meta.url),
  "utf8",
);

describe("update badge and runtime logging contract", () => {
  it("keeps optional update discovery in a global clickable sidebar badge", () => {
    expect(app).toContain('class="sidebar-update-badge"');
    expect(app).toContain('call(\n      "update.check"');
    expect(app).toContain("30 * 60 * 1000");
    expect(app).toContain('hasEntitlement("feature.software_update")');
    expect(app).toContain('v-if="availableUpdate && canUseSoftwareUpdate"');
    expect(app).toContain('@click="openUpdateDetails"');
    expect(app).toContain('@click="startUpdateInstall"');
    expect(app).not.toContain("window.alert");
    expect(app).not.toContain("window.confirm");
  });

  it("records click and navigation events and batches them to the backend", () => {
    expect(app).toContain('document.addEventListener("click", trackUserClick, true)');
    expect(app).toContain('call(\n      "logs.webui-events"');
    expect(app).toContain('kind: "navigation"');
    expect(app).toContain("control_text");
    expect(app).toContain("disabled");
    expect(app).toContain("selector");
    expect(app).not.toContain("mousemove");
    expect(app).not.toContain("scrollY");
  });

  it("sends request origin and marks automatic refresh calls", () => {
    expect(api).toContain('"X-LangLang-Origin": origin');
    expect(api).toContain("withActionOrigin");
    expect(autoRefresh).toContain('withActionOrigin(\n        "auto_refresh"');
  });

  it("exposes log status, exports, directory opening and manual update check", () => {
    expect(more).toContain('call("logs.status"');
    expect(more).toContain('call("logs.export"');
    expect(more).toContain('call("logs.open-dir"');
    expect(more).toContain("导出最近 1 天");
    expect(more).toContain("导出最近 7 天");
    expect(more).toContain("检查更新");
    expect(more).toContain("canViewRuntimeLogs");
    expect(more).toContain("canUseSoftwareUpdate");
    expect(app).toContain('class="more-menu"');
    expect(app).toContain("bootstrap.more_nav");
  });
});

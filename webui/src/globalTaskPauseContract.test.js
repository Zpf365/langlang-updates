import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const app = readFileSync(new URL("./App.vue", import.meta.url), "utf8");
const component = readFileSync(
  new URL("./components/GlobalTaskPauseControl.vue", import.meta.url),
  "utf8",
);

describe("global task pause shell contract", () => {
  it("mounts the control in the persistent app shell outside routed pages", () => {
    expect(app).toContain(
      'import GlobalTaskPauseControl from "./components/GlobalTaskPauseControl.vue";',
    );
    expect(app).toContain("<GlobalTaskPauseControl");
    expect(app.indexOf("<GlobalTaskPauseControl")).toBeLessThan(
      app.indexOf("<RouterView"),
    );
  });

  it("does not gate account switching or logout on the pause state", () => {
    expect(app).toContain(
      ':disabled="accountBusy" @click="prepareAccountAction(\'switch\')"',
    );
    expect(app).toContain(
      ':disabled="accountBusy" @click="prepareAccountAction(\'logout\')"',
    );
    expect(component).not.toContain("account.action");
  });

  it("keeps retry and unknown state visible after status errors", () => {
    expect(component).toContain("任务状态读取失败");
    expect(component).toContain("状态未知");
    expect(component).toContain("global-task-pause-retry");
  });

  it("uses an opaque, high-layer pause confirmation without fade-in transparency", () => {
    expect(component).toContain('mask-class="global-task-pause-mask"');
    expect(component).toContain(':z-index="2400"');
    expect(component).toContain(":mask-style=");
    expect(component).toContain(":panel-style=");
    expect(component).toContain(":global(.global-task-pause-mask) {");
    expect(component).toContain(
      "background: rgba(10, 22, 34, 0.74) !important;",
    );
    expect(component).toContain(
      ":global(.global-task-pause-mask .modal-card) {",
    );
    expect(component).toContain("background: #fff !important;");
    expect(component).toContain("opacity: 1 !important;");
    expect(component).toContain("transition: none !important;");
  });
});

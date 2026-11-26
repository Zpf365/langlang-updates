import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const router = readFileSync(
  new URL("./router.js", import.meta.url),
  "utf8",
);
const app = readFileSync(
  new URL("./App.vue", import.meta.url),
  "utf8",
);

describe("big screen WebUI entry", () => {
  it("opens the legacy dashboard through one backend RPC", () => {
    const openBigScreen = app.slice(
      app.indexOf("async function openBigScreen"),
      app.indexOf("function formatBytes"),
    );
    expect(app).toContain('call("dashboard.big-screen.open"');
    expect(app).toContain("item.key === 'big_screen' ? openBigScreen() : navigate(item)");
    expect(openBigScreen).toContain("bigScreenFeedback.value");
    expect(openBigScreen).not.toContain("error.value");
    expect(app).not.toContain("原版数据大屏");
    expect(app).not.toContain("打开旧版全屏交互逻辑");
    expect(router).not.toContain('path: "/big-screen"');
    expect(app).toContain('big_screen: "🖥"');
    expect(app).toContain("更多");
  });
});

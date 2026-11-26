import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  new URL("./App.vue", import.meta.url),
  "utf8",
);

describe("startup background refresh", () => {
  it("starts one backend scheduler with all four persisted switches", () => {
    expect(source).toContain('call(\n    "startup.refresh.start"');
    expect(source).toContain('"langlang.auto-refresh.stores"');
    expect(source).toContain('"langlang.auto-refresh.orders"');
    expect(source).toContain('"langlang.auto-refresh.products"');
    expect(source).toContain('"langlang.auto-refresh.violations"');
    expect(source).toContain('origin: "startup_refresh"');
    expect(source).toContain("startBackgroundRefresh();");
  });
});

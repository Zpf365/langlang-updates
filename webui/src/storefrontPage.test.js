import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  new URL("./pages/StorefrontPage.vue", import.meta.url),
  "utf8",
);

describe("storefront page contract", () => {
  it("refreshes official homepage data after a confirmed write", () => {
    expect(source).toContain("async function loadStorefront(force = false)");
    expect(source).toContain("await loadStorefront(true)");
    expect(source).toContain('call("storefront.action"');
    expect(source).toContain('call("storefront.load"');
    expect(source).toContain("confirmation.open = false");
  });
});

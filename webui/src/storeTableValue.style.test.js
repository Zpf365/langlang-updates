import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  new URL("./components/StoreSalesValue.vue", import.meta.url),
  "utf8",
);

describe("store sales value colors", () => {
  it("keeps sales green and refund red with the shared subdued tones", () => {
    expect(source).toContain(".sale-tone {");
    expect(source).toContain("color: var(--store-sales-color, #466d5c);");
    expect(source).toContain(".refund-tone {");
    expect(source).toContain("color: var(--store-refund-color, #8a5058);");
    expect(source).not.toContain("background:");
  });
});

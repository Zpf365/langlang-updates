// @vitest-environment jsdom

import { describe, expect, it } from "vitest";

import { markUiReady } from "./updateReady";

describe("hot update ready marker", () => {
  it("marks the document only after the application mounted", () => {
    const target = document.implementation.createHTMLDocument("ready");

    expect(target.documentElement.dataset.langlangReady).toBeUndefined();

    markUiReady(target);

    expect(target.documentElement.dataset.langlangReady).toBe("1");
  });
});

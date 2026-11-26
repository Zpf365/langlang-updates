import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  readRuntimeNoticeVisible,
  runtimeNoticeStorageKey,
  writeRuntimeNoticeVisible,
} from "./runtimeNotice";

class MemoryStorage {
  constructor() {
    this.values = new Map();
  }

  getItem(key) {
    return this.values.get(key) ?? null;
  }

  setItem(key, value) {
    this.values.set(key, String(value));
  }
}

const page = readFileSync(
  new URL("./pages/StoresPage.vue", import.meta.url),
  "utf8",
);

describe("store runtime notice visibility", () => {
  it("defaults to visible and persists hide/show per account and page", () => {
    const storage = new MemoryStorage();
    const accountA = runtimeNoticeStorageKey({
      account_id: "account-a",
    });
    const accountB = runtimeNoticeStorageKey({
      account_id: "account-b",
    });

    expect(accountA).not.toBe(accountB);
    expect(accountA).toContain("stores");
    expect(readRuntimeNoticeVisible(storage, accountA)).toBe(true);

    writeRuntimeNoticeVisible(storage, accountA, false);
    expect(readRuntimeNoticeVisible(storage, accountA)).toBe(false);
    expect(readRuntimeNoticeVisible(storage, accountB)).toBe(true);

    writeRuntimeNoticeVisible(storage, accountA, true);
    expect(readRuntimeNoticeVisible(storage, accountA)).toBe(true);
  });

  it("falls back to visible when storage access is blocked", () => {
    const blocked = {
      getItem() {
        throw new Error("blocked");
      },
      setItem() {
        throw new Error("blocked");
      },
    };
    expect(readRuntimeNoticeVisible(blocked, "key")).toBe(true);
    expect(() => writeRuntimeNoticeVisible(blocked, "key", false)).not.toThrow();
  });

  it("keeps the persistent compact switch and only conditionally hides the toast", () => {
    expect(page).toContain('class="runtime-notice-toggle"');
    expect(page).toContain(':aria-pressed="runtimeNoticeVisible ? \'true\' : \'false\'"');
    expect(page).toContain(":aria-label=\"runtimeNoticeVisible ? '关闭运行提示' : '开启运行提示'\"");
    expect(page).toContain("@click=\"toggleRuntimeNotice\"");
    expect(page).toContain("v-if=\"message && !dialogMode && runtimeNoticeVisible\"");
    expect(page).toContain("writeRuntimeNoticeVisible");
    expect(page).toContain("readRuntimeNoticeVisible");
    expect(page).toContain("max-width: min(560px, calc(100vw - 132px))");
    const toggleStart = page.indexOf("function toggleRuntimeNotice()");
    const toggleEnd = page.indexOf("watch(runtimeNoticeStorageKey", toggleStart);
    expect(toggleStart).toBeGreaterThan(-1);
    expect(toggleEnd).toBeGreaterThan(toggleStart);
    expect(page.slice(toggleStart, toggleEnd)).not.toContain("message.value");
  });
});

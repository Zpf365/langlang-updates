import { describe, expect, it } from "vitest";
import {
  STORE_VIEW_FULL,
  STORE_VIEW_OVERVIEW,
  readStoreViewMode,
  storeViewStorageKey,
  writeStoreViewMode,
} from "./storeViewMode";

class MemoryStorage {
  constructor() {
    this.values = new Map();
  }

  getItem(key) {
    return this.values.get(key) ?? null;
  }

  setItem(key, value) {
    this.values.set(key, value);
  }
}

describe("store view mode", () => {
  it("defaults to the compact overview and persists per account", () => {
    const storage = new MemoryStorage();
    const accountA = storeViewStorageKey({ account_id: "a" });
    const accountB = storeViewStorageKey({ account_id: "b" });

    expect(readStoreViewMode(storage, accountA)).toBe(
      STORE_VIEW_OVERVIEW,
    );
    writeStoreViewMode(storage, accountA, STORE_VIEW_FULL);
    expect(readStoreViewMode(storage, accountA)).toBe(STORE_VIEW_FULL);
    expect(readStoreViewMode(storage, accountB)).toBe(
      STORE_VIEW_OVERVIEW,
    );
  });
});

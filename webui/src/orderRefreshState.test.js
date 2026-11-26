import { describe, expect, it } from "vitest";
import { beginOrderRefresh, finishOrderRefresh } from "./orderRefreshState";

describe("order row refresh state", () => {
  it("rejects duplicate refresh for the same row", () => {
    const active = new Set(["ORDER-1"]);
    const result = beginOrderRefresh(active, "ORDER-1");

    expect(result.started).toBe(false);
    expect([...result.activeIds]).toEqual(["ORDER-1"]);
  });

  it("allows independent rows to refresh concurrently", () => {
    const first = beginOrderRefresh(new Set(), "ORDER-1");
    const second = beginOrderRefresh(first.activeIds, "ORDER-2");

    expect(first.started).toBe(true);
    expect(second.started).toBe(true);
    expect([...second.activeIds]).toEqual(["ORDER-1", "ORDER-2"]);
  });

  it("removes only the completed row from busy state", () => {
    const active = new Set(["ORDER-1", "ORDER-2"]);
    expect([...finishOrderRefresh(active, "ORDER-1")]).toEqual(["ORDER-2"]);
  });
});

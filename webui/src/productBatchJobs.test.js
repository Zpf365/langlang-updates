import { describe, expect, it } from "vitest";

import {
  productSelectionState,
  runProductBatchJob,
} from "./productBatchOperations";

describe("product batch job polling", () => {
  it("starts one store-scoped batch and stops polling at cancelled", async () => {
    const calls = [];
    const statuses = [
      { status: "running", current: 1, total: 2, counts: {} },
      {
        status: "cancelled",
        current: 2,
        total: 2,
        counts: { success: 1, failed: 0, cancelled: 1, pending: 0 },
      },
    ];
    const request = async (action, payload) => {
      calls.push([action, payload]);
      if (action === "products.batch.start") {
        return { job_id: "batch-1" };
      }
      return statuses.shift();
    };
    const updates = [];

    const result = await runProductBatchJob({
      rows: [
        { store_id: 7, product_id: "same-id" },
        { store_id: 7, product_id: "other-id" },
      ],
      action: "delete",
      request,
      onUpdate: (job) => updates.push(job.status),
      wait: async () => {},
    });

    expect(calls).toEqual([
      [
        "products.batch.start",
        {
          action: "delete",
          items: [
            { store_id: 7, product_id: "same-id" },
            { store_id: 7, product_id: "other-id" },
          ],
        },
      ],
      ["products.batch.status", { job_id: "batch-1" }],
      ["products.batch.status", { job_id: "batch-1" }],
    ]);
    expect(updates).toEqual(["running", "cancelled"]);
    expect(result.status).toBe("cancelled");
    expect(result.counts.cancelled).toBe(1);
  });

  it("keeps the job id and retries a transient status failure", async () => {
    const calls = [];
    let statusAttempt = 0;
    const statuses = [
      new Error("temporary gateway unavailable"),
      { status: "running", current: 1, total: 2, counts: { pending: 0 } },
      {
        status: "verification_pending",
        current: 2,
        total: 2,
        counts: { success: 1, failed: 0, cancelled: 0, pending: 1 },
      },
    ];
    const request = async (action, payload) => {
      calls.push([action, payload]);
      if (action === "products.batch.start") return { job_id: "batch-retry" };
      statusAttempt += 1;
      const next = statuses.shift();
      if (next instanceof Error) throw next;
      return next;
    };
    const started = [];
    const updates = [];
    const waits = [];

    const result = await runProductBatchJob({
      rows: [{ store_id: 9, product_id: "product-retry" }],
      action: "delete",
      request,
      onStarted: (job) => started.push(job),
      onUpdate: (job) => updates.push({
        status: job.status,
        jobId: job.job_id,
      }),
      wait: async (delay) => waits.push(delay),
    });

    expect(started).toEqual([
      { job_id: "batch-retry", status: "starting" },
    ]);
    expect(updates.map((row) => row.status)).toEqual([
      "status_unavailable",
      "running",
      "verification_pending",
    ]);
    expect(updates.every((row) => row.jobId === "batch-retry")).toBe(true);
    expect(statusAttempt).toBe(3);
    expect(waits).toHaveLength(2);
    expect(waits[0]).toBeGreaterThan(0);
    expect(waits[1]).toBeGreaterThan(waits[0]);
    expect(result.job_id).toBe("batch-retry");
  });

  it("stops only when the backend explicitly reports the job missing", async () => {
    let statusCalls = 0;
    const request = async (action) => {
      if (action === "products.batch.start") return { job_id: "batch-missing" };
      statusCalls += 1;
      throw new Error("商品批量任务不存在");
    };

    await expect(runProductBatchJob({
      rows: [{ store_id: 9, product_id: "product-missing" }],
      action: "delete",
      request,
      wait: async () => {},
    })).rejects.toThrow("不存在");

    expect(statusCalls).toBe(1);
  });
});

describe("product selection after collection replacement", () => {
  it("drops a selected product that is absent from the refreshed collection", () => {
    const state = productSelectionState(
      ["11:A"],
      [{ store_id: 11, product_id: "B" }],
    );

    expect(state.ids).toEqual([]);
    expect(state.count).toBe(0);
    expect(state.buttonDisabled).toBe(true);
  });
});

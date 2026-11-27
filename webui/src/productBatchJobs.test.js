import { describe, expect, it } from "vitest";

import { runProductBatchJob } from "./productBatchOperations";

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
});

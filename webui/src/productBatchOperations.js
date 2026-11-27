export const PRODUCT_BATCH_TERMINAL_STATUSES = new Set([
  "completed",
  "partial",
  "failed",
  "verification_pending",
  "cancelled",
]);

export function productBatchItems(rows) {
  return rows.map((row) => {
    const storeId = Number(row.store_id);
    const productId = String(row.product_id || "").trim();
    if (!Number.isFinite(storeId) || storeId <= 0 || !productId) {
      throw new Error("商品缺少有效店铺或商品 ID");
    }
    return {
      store_id: storeId,
      product_id: productId,
    };
  });
}

export async function runProductBatchJob({
  rows,
  action,
  request,
  onUpdate = () => {},
  wait = () => new Promise((resolve) => setTimeout(resolve, 300)),
}) {
  const started = await request("products.batch.start", {
    action,
    items: productBatchItems(rows),
  });
  const jobId = String(started?.job_id || "");
  if (!jobId) throw new Error("启动批量任务失败：缺少 job_id");

  let job = null;
  while (true) {
    job = await request("products.batch.status", { job_id: jobId });
    onUpdate(job);
    if (PRODUCT_BATCH_TERMINAL_STATUSES.has(job?.status)) {
      return { ...job, job_id: jobId };
    }
    await wait();
  }
}

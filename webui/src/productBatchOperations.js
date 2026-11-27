export const PRODUCT_BATCH_TERMINAL_STATUSES = new Set([
  "completed",
  "partial",
  "failed",
  "verification_pending",
  "cancelled",
]);

export function productSelectionKey(row) {
  return `${Number(row?.store_id || 0)}:${String(row?.product_id || "")}`;
}

export function productSelectionState(selectedIds, products) {
  const available = new Set((products || []).map(productSelectionKey));
  const ids = [...new Set(selectedIds || [])].filter((id) => available.has(id));
  return {
    ids,
    count: ids.length,
    buttonDisabled: ids.length === 0,
  };
}

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

function isTerminalStatusError(error) {
  const message = String(error instanceof Error ? error.message : error || "");
  return /不存在|不属于当前账户|会话代际|无权|未授权|账户会话正在关闭|账户已切换/.test(
    message,
  );
}

export async function runProductBatchJob({
  rows,
  action,
  request,
  onStarted = () => {},
  onUpdate = () => {},
  wait = () => new Promise((resolve) => setTimeout(resolve, 300)),
}) {
  const started = await request("products.batch.start", {
    action,
    items: productBatchItems(rows),
  });
  const jobId = String(started?.job_id || "");
  if (!jobId) throw new Error("启动批量任务失败：缺少 job_id");
  onStarted({
    job_id: jobId,
    status: String(started?.status || "starting"),
  });

  let retryCount = 0;
  while (true) {
    let job = null;
    try {
      job = await request("products.batch.status", { job_id: jobId });
      retryCount = 0;
    } catch (error) {
      if (isTerminalStatusError(error)) throw error;
      retryCount += 1;
      const delay = Math.min(1600, 200 * (2 ** Math.min(3, retryCount - 1)));
      onUpdate({
        job_id: jobId,
        status: "status_unavailable",
        message: "任务状态暂不可读，正在自动重试；取消入口保持可用",
        retry_count: retryCount,
      });
      await wait(delay);
      continue;
    }
    const publicJob = { ...job, job_id: jobId };
    onUpdate(publicJob);
    if (PRODUCT_BATCH_TERMINAL_STATUSES.has(publicJob.status)) {
      return publicJob;
    }
    await wait(300);
  }
}

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  new URL("./pages/ProductUploadParityPage.vue", import.meta.url),
  "utf8",
);
const templateSource = readFileSync(
  new URL("./components/UploadTemplateDialog.vue", import.meta.url),
  "utf8",
);
const selectionSource = readFileSync(
  new URL("./components/UploadStoreSelectionDialog.vue", import.meta.url),
  "utf8",
);

describe("upload page contract", () => {
  it("connects every former placeholder action to a real RPC", () => {
    for (const action of [
      "upload.batch.import",
      "upload.batch.reparse",
      "upload.batch.preflight",
      "upload.batch.export",
      "upload.batch.clear",
      "upload.tasks.create",
      "upload.tasks.retry",
      "upload.tasks.clear",
      "upload.run.start",
      "upload.run.pause",
      "upload.run.cancel",
      "upload.logs",
    ]) {
      expect(source).toContain(`call("${action}"`);
    }
    expect(source).not.toContain("@click=\"action(");
    expect(source).not.toContain("window.confirm");
    expect(source).not.toContain("upload.links.");
    expect(source).not.toContain("登录 1688 读取商品");
    expect(source).not.toContain("1688 链接采集");
    expect(source).not.toContain("解析校验");
    expect(source).toContain("本地预检");
    expect(source).toContain("导入数据包");
    expect(source).toContain("导入文件夹");
    expect(source).toContain("重新解析预览");
    expect(source).toContain("TXT / 微信 JSON 数据包");
    expect(source).toContain("按内容自动识别");
    expect(source).toContain("尚未向平台提交");
  });

  it("shows live task progress and deferred pause/cancel states", () => {
    expect(source).toContain("uploadProgress");
    expect(source).toContain("uploadJob.completed");
    expect(source).toContain("uploadJob.total");
    expect(source).toContain("active_run_job");
    expect(source).toContain("applyUploadJob(activeJob)");
    expect(source).toContain("pollingJobId");
    expect(source).toContain("当前上传 ·");
    expect(source).toContain("运行中 ${uploadJob.completed}/${uploadJob.total}");
    expect(source).toContain("retryableItemCount");
    expect(source).toContain("isRetryableItem");
    expect(source).toContain("product_image_upload");
    expect(source).toContain("preflightMappingText");
    expect(source).toContain("类目自动匹配");
    expect(source).not.toContain("linkProgress");
    expect(source).not.toContain("hasLinkEvidence");
    expect(source).not.toContain("成功商品");
    expect(source).not.toContain("失败原因");
    expect(source).toContain("async function pauseUpload");
    expect(source).toContain("function cancelUpload");
    expect(source).toContain("当前在途商品处理结束前不会显示为已停止");
    expect(source).toContain("待核实商品未重试");
  });

  it("surfaces category application and durable waiting states", () => {
    expect(templateSource).toContain("目标类目未开通时强制自动申请并重试");
    expect(source).toContain("自动申请中");
    expect(source).toContain("类目生效·重试中");
    expect(source).toContain("等待类目");
    expect(source).toContain("categoryStatusDetail");
    expect(source).toContain("category_statuses");
    expect(source).toContain("category_next_check_at");
    expect(source).toContain("category_reprocess_available");
    expect(source).toContain("类目失败·可重试");
  });

  it("exposes category auto-resume control and deposit wait feedback", () => {
    expect(source).toContain("upload.category-auto-resume.set");
    expect(source).toContain("upload.waiting-category.cancel");
    expect(source).toContain("类目通过后自动续传");
    expect(source).toContain("等待补缴保证金");
    expect(source).toContain("等待类目审核");
    expect(source).toContain("缺少类目资质");
    expect(source).toContain("取消等待");
    expect(source).toContain("category_checks");
    expect(source).toContain("category_deposit_snapshot");
    expect(source).toContain("operation_required_deposit");
    expect(source).toContain("平台支持免押试用");
    expect(source).toContain("category_previous_failure_message");
    expect(source).toContain("官方原文");
    expect(templateSource).toContain("默认保留数据包已有的官方类目");
    expect(templateSource).toContain("最高保证金仅用于合规候选排序和提示");
    expect(templateSource).toContain("强制兜底");
    expect(templateSource).toContain("保证金上限填 0 表示不限制");
  });

  it("uses the dedicated store selection workflow and shows real submission summary", () => {
    expect(source).toContain("UploadStoreSelectionDialog");
    expect(source).toContain("选择店铺并创建任务");
    expect(source).not.toContain("row-store-select");
    expect(selectionSource).toContain("选择分配店铺");
    expect(selectionSource).toContain("平均分配");
    expect(selectionSource).toContain("每个店铺都上传");
    expect(selectionSource).toContain("全选当前页");
    expect(selectionSource).toContain("全选筛选结果");
    expect(selectionSource).toContain("统一设置运费模板");
    expect(selectionSource).toContain("统一设置品牌");
    expect(selectionSource).toContain("统一设置售后地址");
    expect(selectionSource).toContain("生成并开始上传");
    expect(selectionSource).toContain('v-if="selectedStoreIds.includes(Number(store.id))"');
    expect(selectionSource).toContain('class="store-config-placeholder"');
    expect(selectionSource).toContain("勾选后展开运费模板、售后地址和品牌");
    expect(selectionSource).toContain("无品牌");
    expect(selectionSource).toContain("function configValue");
    expect(selectionSource).toContain(":value=\"configValue(store.id, 'freight_template_id')\"");
    expect(selectionSource).toContain("setConfig(store.id, 'freight_template_id'");
    expect(source).toContain("upload.store-options");
    expect(source).toContain("运费模板");
    expect(source).toContain("售后地址");
    expect(source).toContain("assignments,");
    expect(source).toContain("store_options:");
    expect(source).toContain("goToTasks");
    expect(source).toContain('tab: "tasks"');
    expect(selectionSource).not.toContain("{{ option.label }} · {{ option.id }}");
    expect(source).toContain("目标店铺：${targetStores.join");
    expect(source).toContain("最高保证金：");
    expect(source).toContain("去重方式：");
    expect(source).toContain("开始真实提交");
    expect(templateSource).toContain("category_business_type");
  });
});

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const page = readFileSync(
  new URL("./pages/ProductUploadParityPage.vue", import.meta.url),
  "utf8",
);
const dialog = readFileSync(
  new URL("./components/UploadTemplateDialog.vue", import.meta.url),
  "utf8",
);

describe("original upload template layout contract", () => {
  it("uses a table rather than the old card wall", () => {
    expect(page).toContain('class="data-table template-table"');
    for (const heading of ["模板名称", "标题规则", "价格规则", "默认库存", "发布方式", "更新时间"]) {
      expect(page).toContain(heading);
    }
    expect(page).not.toContain("完整模板 JSON");
  });

  it("keeps the original list actions and multi-tab form", () => {
    for (const label of ["添加上货模板", "进入上传商品", "编辑模板", "复制模板", "设为默认", "删除模板"]) {
      expect(page).toContain(label);
    }
    expect(page).toContain('@dblclick="openEditTemplate(template)"');
    for (const tab of ["基础设置", "商品过滤", "属性设置"]) {
      expect(dialog).toContain(tab);
    }
    for (const label of [
      "发布与价格",
      "标题处理",
      "视频来源",
      "服务与发布方式",
      "标签图资料",
      "发货设置",
      "标题替换",
      "兜底类目",
      "低价引流保护",
      "编辑阶梯倍率",
      "云违规设置",
    ]) {
      expect(dialog).toContain(label);
    }
    expect(dialog).not.toContain("完整模板 JSON");
  });

  it("bridges the video folder through the real desktop RPC", () => {
    expect(dialog).toContain('call("upload.templates.select-folder"');
    expect(dialog).toContain("initial_path");
    expect(dialog).toContain("if (result.path)");
  });
});

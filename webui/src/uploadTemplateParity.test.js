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
const storeDialog = readFileSync(
  new URL("./components/UploadStoreSelectionDialog.vue", import.meta.url),
  "utf8",
);

describe("original upload template layout contract", () => {
  it("defaults a new template to cloud violation filtering", () => {
    expect(dialog).toContain("cloud_violation: true");
  });

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

  it("keeps the specified-category cascade in the template editor only", () => {
    for (const label of [
      "指定商品类目",
      "请选择要指定的类目",
      "保留来源类目；来源缺失时使用官方自动匹配",
      "一级大类",
      "二级类目",
      "三级叶子",
      "重新读取",
    ]) {
      expect(dialog).toContain(label);
    }
    expect(storeDialog).not.toContain("指定商品类目");
    expect(storeDialog).not.toContain("请选择要指定的类目");
    expect(storeDialog).not.toContain("specified_category_ids");
    expect(page).toContain('call("upload.categories"');
    expect(page).toContain("mergeUploadStoreOptions");
    expect(page).toContain("@load-categories=");
    expect(dialog).toContain("specified_category_ids");
    expect(dialog).toContain("specified_category_id");
    expect(dialog).toContain("specified_category_path");
    expect(dialog).toContain("specified_category_version");
    expect(dialog).toContain("category_mode");
    expect(dialog).toContain('class="input category-trigger"');
    expect(storeDialog).toContain("brand_mode");
    expect(dialog).toContain("leaf.available === false");
    expect(dialog).toContain("未生效");
    expect(dialog).toContain('@click="selectCategoryRoot(root)"');
    expect(dialog).toContain('@click="selectCategoryParent(parent)"');
    expect(dialog).toContain('@click="chooseCategory(leaf)"');
  });

  it("shows image position controls with zero as no removal", () => {
    for (const label of [
      "图片/视频处理",
      "主图",
      "详情图",
      "移除前",
      "移除后",
      "只保留前",
      "0 表示不额外移除",
    ]) {
      expect(dialog).toContain(label);
    }
    for (const key of [
      "remove_head_index",
      "remove_detail_index",
      "trim_detail_tail",
      "detail_keep_first",
    ]) {
      expect(dialog).toContain(key);
    }
  });
});

  it("keeps every new-template image removal rule non-destructive", () => {
    expect(dialog).toContain("trim_head: 0");
    expect(dialog).toContain("remove_head_index: 0");
    expect(dialog).toContain("remove_detail_index: 0");
    expect(dialog).toContain("trim_detail: 0");
    expect(dialog).toContain("trim_detail_tail: 0");
    expect(dialog).toContain("detail_keep_first: 0");
  });

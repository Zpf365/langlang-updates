import { describe, expect, it } from "vitest";
import {
  applyImageRules,
  buildUploadTemplatePayload,
  validateLadderRules,
  validateLicenseGroups,
  yuanToCents,
} from "./uploadTemplate";

function baseForm(overrides = {}) {
  return {
    name: "原版模板",
    price_multiplier: 1.25,
    final_price_decimals: 2,
    stock: 1000,
    deposit_limit: 300,
    low_price_multiplier: 2,
    trim_head: 0,
    trim_detail: 2,
    trim_detail_tail: 2,
    detail_keep_first: 0,
    remove_head_index: 0,
    remove_detail_index: 0,
    category_license_groups: "",
    ladder_rules: "",
    cloud_violation_types: "",
    ...overrides,
  };
}

describe("upload template editor calculations", () => {
  it("converts yuan inputs to integer cents without losing fixed price", () => {
    expect(yuanToCents("12.345", "加价")).toBe(1235);
    const payload = buildUploadTemplatePayload({
      form: baseForm({
        fixed_price_enabled: true,
        category_business_type: "individual",
        custom_future_field: "must-survive",
      }),
      sourceTemplate: { id: "tpl-old", legacy_field: "must-survive" },
      priceOffsetYuan: "3.20",
      fixedPriceYuan: "99.90",
    });
    expect(payload.price_offset).toBe(320);
    expect(payload.fixed_price).toBe(9990);
    expect(payload.final_price_decimals).toBe(2);
    expect(payload.legacy_field).toBe("must-survive");
    expect(payload.category_business_type).toBe("individual");
    expect(payload.custom_future_field).toBe("must-survive");
  });

  it("accepts the original four-part ladder syntax and rejects malformed rules", () => {
    expect(validateLadderRules("0=49.99=1.2=3\n49.99=99.99=1.15=0")).toBe(true);
    expect(() => validateLadderRules("0=49.99=1.2")).toThrow("每行必须填写");
    expect(() => validateLadderRules("100=20=1.2=0")).toThrow("价格区间");
    expect(() => validateLadderRules("0=20=0=0")).toThrow("价格区间");
  });

  it("requires certificate groups to remain a JSON array", () => {
    expect(validateLicenseGroups("")).toBe("");
    expect(validateLicenseGroups('[{"name":"营业执照"}]')).toContain("营业执照");
    expect(() => validateLicenseGroups('{"name":"营业执照"}')).toThrow("JSON 数组");
  });

  it("rejects invalid decimal, stock and negative price inputs", () => {
    expect(() => buildUploadTemplatePayload({
      form: baseForm({ final_price_decimals: 5 }),
      priceOffsetYuan: "0",
      fixedPriceYuan: "0",
    })).toThrow("0–4");
    expect(() => buildUploadTemplatePayload({
      form: baseForm({ stock: -1 }),
      priceOffsetYuan: "0",
      fixedPriceYuan: "0",
    })).toThrow("统一库存");
    expect(() => yuanToCents("-0.01", "固定价格")).toThrow("非负数字");
  });

  it("uses the same ordered image rules as the backend", () => {
    const result = applyImageRules(
      {
        head_imgs: ["h1", "h2", "h3", "h4", "h5"],
        desc_info: { imgs: ["d1", "d2", "d3", "d4", "d5", "d6", "d7", "d8"] },
      },
      {
        remove_head_index: 2,
        trim_head: 1,
        remove_detail_index: 2,
        trim_detail: 2,
        trim_detail_tail: 2,
        detail_keep_first: 2,
      },
    );
    expect(result.head_imgs).toEqual(["h3", "h4", "h5"]);
    expect(result.desc_info.imgs).toEqual(["d4", "d5"]);
    expect(applyImageRules(result, {
      remove_head_index: 2,
      trim_head: 1,
      remove_detail_index: 2,
      trim_detail: 2,
      trim_detail_tail: 2,
      detail_keep_first: 2,
    })).toEqual(result);
  });

  it("normalizes image controls without treating zero as an image index", () => {
    const payload = buildUploadTemplatePayload({
      form: baseForm({
        remove_head_index: 0,
        remove_detail_index: 0,
        trim_detail: 2,
        trim_detail_tail: 2,
        detail_keep_first: 0,
      }),
      priceOffsetYuan: "0",
      fixedPriceYuan: "0",
    });
    expect(payload.remove_head_index).toBe(0);
    expect(payload.remove_detail_index).toBe(0);
    expect(payload.trim_detail).toBe(2);
    expect(payload.trim_detail_tail).toBe(2);
    expect(payload.detail_keep_first).toBe(0);
  });

  it("keeps an optional three-level category snapshot with the template", () => {
    const selected = buildUploadTemplatePayload({
      form: baseForm({
        category_mode: "specified",
        specified_category_id: "3003",
        specified_category_ids: ["1000", "2000", "3003"],
        specified_category_path: "服饰 / 女装 / 连衣裙",
        specified_category_version: "tree-v7",
        specified_category_store_id: "19",
      }),
      sourceTemplate: { id: "tpl-category" },
      priceOffsetYuan: "0",
      fixedPriceYuan: "0",
    });
    expect(selected.category_mode).toBe("specified");
    expect(selected.specified_category_id).toBe("3003");
    expect(selected.specified_category_ids).toEqual(["1000", "2000", "3003"]);
    expect(selected.specified_category_path).toBe("服饰 / 女装 / 连衣裙");
    expect(selected.specified_category_version).toBe("tree-v7");
    expect(selected.specified_category_store_id).toBe("19");

    const automatic = buildUploadTemplatePayload({
      form: baseForm({
        category_mode: "auto",
        specified_category_id: "",
        specified_category_ids: [],
        specified_category_path: "",
        specified_category_version: "",
        specified_category_store_id: "",
      }),
      priceOffsetYuan: "0",
      fixedPriceYuan: "0",
    });
    expect(automatic.category_mode).toBe("auto");
    expect(automatic.specified_category_ids).toEqual([]);
  });
});

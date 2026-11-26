import { describe, expect, it } from "vitest";
import {
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
    trim_detail: 0,
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
});

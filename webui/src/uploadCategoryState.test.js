import { describe, expect, it } from "vitest";
import {
  categoryApplicationLabel,
  categoryApplicationReason,
  categorySelectionMatchesStore,
  categorySelectable,
} from "./uploadCategoryState";

describe("upload category application states", () => {
  it("allows an eligible category that has not been applied for", () => {
    const leaf = {
      available: false,
      selectable: true,
      application_state: "not_applied",
      application_label: "未申请",
      application_reason: "上货启动后自动申请",
    };

    expect(categorySelectable(leaf)).toBe(true);
    expect(categoryApplicationLabel(leaf)).toBe("未申请，上货时申请");
    expect(categoryApplicationReason(leaf)).toBe("上货启动后自动申请");
  });

  it("keeps blocked and unknown categories unavailable", () => {
    const states = [
      ["qualification_required", "缺资质"],
      ["deposit_required", "保证金不足"],
      ["rejected", "平台已拒绝"],
      ["unavailable", "不可申请"],
      ["unknown", "状态未知"],
    ];

    for (const [state, label] of states) {
      const leaf = {
        available: false,
        selectable: false,
        application_state: state,
        application_label: label,
        application_reason: "平台返回的具体原因",
      };
      expect(categorySelectable(leaf)).toBe(false);
      expect(categoryApplicationLabel(leaf)).toBe(label);
      expect(categoryApplicationReason(leaf)).toContain("具体原因");
    }
  });

  it("reports pending review without claiming the category is effective", () => {
    const leaf = {
      available: false,
      selectable: true,
      application_state: "pending",
      application_label: "审核中",
    };

    expect(categorySelectable(leaf)).toBe(true);
    expect(categoryApplicationLabel(leaf)).toBe("审核中，上货时等待");
  });

  it("uses legacy availability only when no selectable decision exists", () => {
    expect(categorySelectable({ available: true })).toBe(true);
    expect(categorySelectable({ available: false })).toBe(false);
    expect(
      categorySelectable({
        available: true,
        selectable: false,
      }),
    ).toBe(false);
  });

  it("detects a saved category that belongs to another store", () => {
    expect(categorySelectionMatchesStore("", "19")).toBe(true);
    expect(categorySelectionMatchesStore("19", "19")).toBe(true);
    expect(categorySelectionMatchesStore("18", "19")).toBe(false);
  });
});

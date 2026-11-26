import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const violations = readFileSync(
  new URL("./pages/ViolationsParityPage.vue", import.meta.url),
  "utf8",
);
const upload = readFileSync(
  new URL("./pages/ProductUploadParityPage.vue", import.meta.url),
  "utf8",
);

describe("violation center image and upload title contract", () => {
  it("auto-refreshes the current logged-in store and never substitutes sample data", () => {
    expect(violations).toContain("loggedInStores");
    expect(violations).toContain("refreshViolations({ automatic: true })");
    expect(violations).toContain("没有已登录的真实店铺");
    expect(violations).not.toMatch(/\b(mock|sample|fixture)(Violation|Data)/i);
  });

  it("shows an explicit cloud-not-deployed or sync-failure state", () => {
    expect(violations).toContain("云端未部署/同步失败");
    expect(violations).toContain("cloud_counterfeit");
    expect(violations).toContain("syncState");
    expect(violations).toContain("if (cloud.skipped)");
  });

  it("shows real product thumbnails and opens a larger preview", () => {
    expect(violations).toContain("row.head_img");
    expect(violations).toContain("openImagePreview");
    expect(violations).toContain("<ModalDialog");
    expect(violations).toContain("previewImage");
  });

  it("surfaces the counterfeit title library on the upload page", () => {
    expect(upload).toContain("violationTitleCount.value = violationTitles.value.length");
    expect(upload).toContain("violation_titles");
    expect(upload).toContain("activeStoreIds");
    expect(upload).toContain("云违规标题库");
    expect(upload).toContain("同标题禁止上传");
  });

  it("enforces the title block in preflight, task creation and submit entry points", () => {
    expect(upload).toContain("enforceViolationPreflight");
    expect(upload).toContain("blockSelectedViolationTitles");
    expect(upload).toContain('call("upload.tasks.create"');
    expect(upload).toContain('call("upload.run.start"');
  });
});

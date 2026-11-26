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
  it("shows real product thumbnails and opens a larger preview", () => {
    expect(violations).toContain("row.head_img");
    expect(violations).toContain("openImagePreview");
    expect(violations).toContain("<ModalDialog");
    expect(violations).toContain("previewImage");
  });

  it("surfaces the counterfeit title library on the upload page", () => {
    expect(upload).toContain("violation_title_count");
    expect(upload).toContain("violation_titles");
    expect(upload).toContain("云违规标题库");
    expect(upload).toContain("同标题禁止上传");
  });
});

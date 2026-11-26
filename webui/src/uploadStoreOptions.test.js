import { describe, expect, it } from "vitest";
import { mergeUploadStoreOptions } from "./uploadStoreOptions.js";

describe("upload store option merging", () => {
  it("keeps an already loaded official category tree when base store options arrive late", () => {
    const existing = {
      "19": {
        freight_templates: [{ id: "1", label: "默认运费" }],
        category_options: {
          version: "tree-v1",
          roots: [{ category_id: "root", children: [] }],
        },
      },
    };
    const incoming = {
      "19": {
        freight_templates: [{ id: "2", label: "新运费" }],
        error: "",
      },
    };

    const merged = mergeUploadStoreOptions(existing, incoming);

    expect(merged["19"].freight_templates[0].id).toBe("2");
    expect(merged["19"].category_options).toEqual(existing["19"].category_options);
  });

  it("accepts a category tree supplied by the later response", () => {
    const merged = mergeUploadStoreOptions(
      { "19": { category_options: { roots: [] } } },
      { "19": { category_options: { roots: [{ category_id: "new" }] } } },
    );

    expect(merged["19"].category_options.roots).toHaveLength(1);
    expect(merged["19"].category_options.roots[0].category_id).toBe("new");
  });
});

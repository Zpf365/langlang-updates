import { describe, expect, it } from "vitest";

import { applyUploadTemplateTitleRules } from "./uploadTitleRules";

describe("upload template title replacements", () => {
  it("leaves the source title unchanged when replacement rules are empty", () => {
    expect(
      applyUploadTemplateTitleRules(
        "2004款 2004系列",
        { title_replace_rules: "" },
      ),
    ).toBe("2004款 2004系列");
  });

  it("replaces every occurrence in the same title", () => {
    expect(
      applyUploadTemplateTitleRules(
        "2004款 2004系列 2004纪念版",
        { title_replace_rules: "2004=2025" },
      ),
    ).toBe("2025款 2025系列 2025纪念版");
  });

  it("runs multiline rules from top to bottom", () => {
    expect(
      applyUploadTemplateTitleRules(
        "2004款 2000系列",
        { title_replace_rules: "2004=2025\n2000=2025" },
      ),
    ).toBe("2025款 2025系列");
    expect(
      applyUploadTemplateTitleRules(
        "2000款",
        { title_replace_rules: "2000=2004\n2004=2025" },
      ),
    ).toBe("2025款");
  });

  it("deletes a fragment when its replacement value is empty", () => {
    expect(
      applyUploadTemplateTitleRules(
        "2004旧款棉柔巾",
        { title_replace_rules: "2004=\n旧款=" },
      ),
    ).toBe("棉柔巾");
  });

  it("keeps equals signs in replacement text", () => {
    expect(
      applyUploadTemplateTitleRules(
        "款号A01",
        { title_replace_rules: "A01=AB=CD" },
      ),
    ).toBe("款号AB=CD");
  });

  it.each([
    ["AB$&CD", "前AB$&CD后"],
    ["AB$$CD", "前AB$$CD后"],
    ["AB$`CD", "前AB$`CD后"],
    ["AB$'CD", "前AB$'CD后"],
  ])("keeps dollar patterns literal in %s", (replacement, expected) => {
    expect(
      applyUploadTemplateTitleRules(
        "前A01后",
        { title_replace_rules: `A01=${replacement}` },
      ),
    ).toBe(expected);
  });

  it("applies replacements before the configured prefix and suffix", () => {
    expect(
      applyUploadTemplateTitleRules(
        "【促销】2004款",
        {
          strip_title_brackets: true,
          title_replace_rules: "2004=2025",
          title_prefix: "新品·",
          title_suffix: " 现货",
        },
      ),
    ).toBe("新品·2025款 现货");
  });
});

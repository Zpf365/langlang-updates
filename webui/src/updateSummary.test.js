import { describe, expect, it } from "vitest";
import {
  normalizeUpdateSummary,
  parseNotesSummary,
  updatePointCount,
} from "./updateSummary";

const longNotes = `
# 澜琅 1.8.84

## 订单与店铺
- 页签数量按店铺筛选。
- 批量登录只打开一次窗口。

## 销售与退款
- 官方销售扣除未领取礼物。
- 退款单量来自真实售后缓存。

## 店铺总览
- 默认经营总览无横向滚动。
- 详情行内展开。

## 数据大屏
- 恢复原版数据大屏。

## 边界
- 代码路径已验证，生产写未实测。

\`\`\`
SHA256 technical
\`\`\`
`;

describe("update summary", () => {
  it("keeps at most six high-level points and skips technical noise", () => {
    const summary = parseNotesSummary(longNotes);
    expect(updatePointCount(summary)).toBe(6);
    expect(summary.flatMap((group) => group.items).join(" ")).not.toContain("SHA256");
    expect(summary.flatMap((group) => group.items).join(" ")).not.toContain("生产写未实测");
  });

  it("accepts an explicit compact manifest summary", () => {
    const summary = normalizeUpdateSummary([
      {
        title: "主要修复",
        items: ["更新弹窗改为宽屏", "底部按钮固定可见"],
      },
    ], longNotes);
    expect(summary).toEqual([
      {
        title: "主要修复",
        items: ["更新弹窗改为宽屏", "底部按钮固定可见"],
      },
    ]);
  });

  it("limits explicit summaries to six points", () => {
    const summary = normalizeUpdateSummary(
      Array.from({ length: 10 }, (_, index) => `要点 ${index + 1}`),
      "",
    );
    expect(updatePointCount(summary)).toBe(6);
  });
});

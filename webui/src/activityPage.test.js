import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  new URL("./pages/ActivityCenterPage.vue", import.meta.url),
  "utf8",
);

describe("activity page contract", () => {
  it("restores all six old activity-center tabs", () => {
    for (const label of [
      "平台活动",
      "限时限量购活动",
      "优惠券活动",
      "限时限量购续期",
      "优惠券管理",
      "执行记录",
    ]) {
      expect(source).toContain(label);
    }
    expect(source).toContain(".activity-tabs { flex: 0 0 auto;");
    expect(source).toContain(".activity-panel { flex: 0 0 auto;");
    expect(source).toContain("progressHideTimer");
    expect(source).toContain('aria-label="关闭进度"');
    expect(source).toContain("max-height: 180px");
  });

  it("connects platform activity workflow buttons to real RPCs", () => {
    for (const action of [
      "activity.catalog",
      "activity.apply",
      "activity.preview",
      "activity.submit-items",
      "activity.remove-all",
      "activity.operations",
      "products.refresh.start",
    ]) {
      expect(source).toContain(`call("${action}"`);
    }
    expect(source).toContain("确认申报并提交");
    expect(source).toContain("上下架商品预检");
    expect(source).toContain("重试失败店铺");
    expect(source).not.toContain("window.confirm");
    expect(source).not.toContain("功能开发中");
  });

  it("keeps selected stores isolated for limited and coupon writes", () => {
    expect(source).toContain('call("activity.snapup.create"');
    expect(source).toContain('call("activity.snapup.renew"');
    expect(source).toContain('call("activity.snapup.delete"');
    expect(source).toContain('call("activity.coupons.create"');
    expect(source).toContain('call("activity.coupons.renew"');
    expect(source).toContain('call("activity.coupons.status"');
    expect(source).toContain("groupRowsByStore(rows)");
    expect(source).toContain("groupLimitedByStore(rows)");
    expect(source).toContain("selectedStores.value");
    expect(source).toContain("官方限时抢购接口禁止设置抢购库存");
    expect(source).toContain('max="0" disabled');
  });

  it("connects automatic renewal to persistent backend scheduler", () => {
    expect(source).toContain('call("activity.renewal.status"');
    expect(source).toContain('call("activity.renewal.save"');
    expect(source).toContain('call("activity.renewal.scan.start"');
    expect(source).toContain('call("activity.renewal.scan.status"');
    expect(source).toContain("智能限时活动托管");
    expect(source).toContain("智能限时活动托管总开关");
    expect(source).toContain("保存为智能活动规则");
    expect(source).toContain("auto_create_runs");
    expect(source).toContain("next_scan_at");
    expect(source).toContain("verification_pending");
    expect(source).toContain("小时进入续期检查");
    expect(source).not.toContain("小时续期</label>");
  });

  it("keeps coupon forms, confirmations and required fields", () => {
    expect(source).toContain("function requestCreateCoupons()");
    expect(source).toContain("requestCouponStatus");
    expect(source).toContain("discount_fee_yuan");
    expect(source).toContain("limit_num_one_person");
    expect(source).toContain("valid_day_num");
    expect(source).toContain("确认创建优惠券");
    expect(source).toContain("确认停用优惠券");
    expect(source).toContain("确认删除优惠券");
  });

  it("keeps persistent activity prefill drafts", () => {
    expect(source).toContain('call("activity.double11.preview"');
    expect(source).toContain('call("activity.double11.drafts.list"');
    expect(source).toContain('call("activity.double11.drafts.save"');
    expect(source).toContain('call("activity.double11.submit"');
    expect(source).toContain("activity.remove-all");
    expect(source).toContain("exportPrefillDrafts");
  });
});

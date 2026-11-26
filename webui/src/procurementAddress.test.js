import { describe, expect, it } from "vitest";
import {
  procurementAddressForm,
  procurementAddressText,
} from "./procurementAddress.js";

describe("procurement address form", () => {
  it("prefers the saved complete address over a masked order refresh", () => {
    const form = procurementAddressForm(
      {
        procurement_address: {
          user_name: "张三",
          tel_number: "13800138000",
          province_name: "浙江省",
          city_name: "杭州市",
          county_name: "西湖区",
          street_name: "",
          detail_info: "文三路88号",
        },
        order: {
          buyer_name: "张*",
          phone: "138****0000",
          address: "浙江省杭州市****",
          address_info: {
            user_name: "张*",
            tel_number: "138****0000",
            province_name: "浙江省",
            city_name: "杭州市",
            county_name: "",
            detail_info: "****",
          },
        },
      },
      { buyer_name: "张*", phone: "138****0000" },
    );

    expect(form).toMatchObject({
      user_name: "张三",
      tel_number: "13800138000",
      province_name: "浙江省",
      city_name: "杭州市",
      county_name: "西湖区",
      detail_info: "文三路88号",
    });
    expect(procurementAddressText(form)).toBe(
      "张三 13800138000 浙江省 杭州市 西湖区 文三路88号",
    );
  });

  it("keeps the masked official values when no plaintext cache exists", () => {
    const form = procurementAddressForm({
      procurement_address: {},
      order: {
        buyer_name: "张*",
        phone: "138****0000",
        address: "浙江省杭州市****",
        address_info: {
          user_name: "张*",
          tel_number: "138****0000",
          province_name: "浙江省",
          city_name: "杭州市",
          county_name: "",
          detail_info: "****",
        },
      },
    });

    expect(form.user_name).toBe("张*");
    expect(form.county_name).toBe("");
    expect(form.detail_info).toBe("****");
  });
});

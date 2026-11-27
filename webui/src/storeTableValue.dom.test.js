// @vitest-environment jsdom

import { createApp, h, nextTick } from "vue";
import { afterEach, describe, expect, it } from "vitest";

import StoreExposureValue from "./components/StoreExposureValue.vue";
import StoreSalesValue from "./components/StoreSalesValue.vue";

const mounted = [];
async function mount(component, props) {
  const host = document.createElement("div");
  document.body.append(host);
  const app = createApp({
    render: () => h(component, props),
  });
  app.mount(host);
  mounted.push({ app, host });
  await nextTick();
  return host;
}

afterEach(() => {
  for (const { app, host } of mounted.splice(0)) {
    app.unmount();
    host.remove();
  }
});

describe("store table value components", () => {
  it("renders a single centered sales line without refund text", async () => {
    const host = await mount(StoreSalesValue, {
      display: {
        salesText: "53.00/1",
        refundText: "0.00/0",
        showRefund: false,
        singleLine: true,
        salesUnknown: false,
        refundUnknown: false,
      },
    });

    expect(host.querySelector(".store-sales-value").classList.contains("single-line")).toBe(true);
    expect(host.querySelectorAll(".store-sale-line")).toHaveLength(1);
    expect(host.querySelectorAll(".store-refund-line")).toHaveLength(0);
    expect(host.textContent).not.toContain("¥");
    expect(host.textContent).not.toMatch(/销|退|单/);
  });

  it("renders equal-sized green and red lines when refund exists", async () => {
    const host = await mount(StoreSalesValue, {
      display: {
        salesText: "0.00/0",
        refundText: "12.00/1",
        showRefund: true,
        singleLine: false,
        salesUnknown: false,
        refundUnknown: false,
      },
    });

    expect(host.querySelector(".store-sales-value").classList.contains("double-line")).toBe(true);
    expect(host.querySelector(".store-sale-line").textContent).toBe("0.00/0");
    expect(host.querySelector(".store-refund-line").textContent).toBe("12.00/1");
    expect(host.querySelector(".store-sale-line").classList.contains("sale-tone")).toBe(true);
    expect(host.querySelector(".store-refund-line").classList.contains("refund-tone")).toBe(true);
  });

  it("marks unknown sales and refund pairs in gray", async () => {
    const host = await mount(StoreSalesValue, {
      display: {
        salesText: "—/—",
        refundText: "—/—",
        showRefund: true,
        singleLine: false,
        salesUnknown: true,
        refundUnknown: true,
      },
    });

    expect(
      host.querySelector(".store-sale-line").classList.contains("unknown"),
    ).toBe(true);
    expect(
      host.querySelector(".store-refund-line").classList.contains("unknown"),
    ).toBe(true);
  });

  it("renders exposure pair and distinguishes unknown from zero", async () => {
    const unknown = await mount(StoreExposureValue, {
      display: { text: "—/—", unknown: true, title: "" },
    });
    expect(unknown.querySelector(".store-exposure-value").classList.contains("unknown")).toBe(true);
    expect(unknown.textContent.trim()).toBe("—/—");

    const zero = await mount(StoreExposureValue, {
      display: { text: "0/0", unknown: false, title: "" },
    });
    expect(zero.querySelector(".store-exposure-value").classList.contains("unknown")).toBe(false);
    expect(zero.textContent.trim()).toBe("0/0");
  });
});

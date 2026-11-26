import { describe, expect, it } from "vitest";
import {
  calculateRefundScenario,
  calculateNoReplyRateAfterNewReplies,
  calculateReviewScenario,
  calculateServiceNoReply,
  calculateTrafficRequirement,
} from "./operationsCalculations";

describe("operations calculators", () => {
  it("calculates review dilution and blocks impossible assumptions", () => {
    const result = calculateReviewScenario({
      total: 36,
      negative: 1,
      targetPercent: 2,
      reviewRatePercent: 30,
      newNegativeRatePercent: 0,
      orders: 10,
    });
    expect(result.required).toBe(47);
    expect(result.rows.find((row) => row.orders === 10).rate).toBeCloseTo(2.56, 2);
    expect(
      calculateReviewScenario({
        total: 36,
        negative: 1,
        targetPercent: 2,
        reviewRatePercent: 30,
        newNegativeRatePercent: 3,
      }).required,
    ).toBeNull();
  });

  it("calculates quality-refund dilution", () => {
    expect(
      calculateRefundScenario({
        orders: 200,
        refunds: 12,
        targetPercent: 5,
        newRefundRatePercent: 2,
      }).required,
    ).toBe(67);
  });

  it("calculates traffic requirement", () => {
    const result = calculateTrafficRequirement({
      targetOrders: 5,
      clickRatePercent: 4,
      payConversionPercent: 1.5,
    });
    expect(result.visits).toBe(334);
    expect(result.exposures).toBe(8350);
  });

  it("calculates service no-reply remediation", () => {
    const result = calculateServiceNoReply({
      total: 120,
      missed: 36,
      targetPercent: 10,
    });

    expect(result.required).toBe(240);
    expect(calculateNoReplyRateAfterNewReplies(120, 36, 24)).toBe(25);
    expect(calculateNoReplyRateAfterNewReplies(120, 36, 27)).toBeCloseTo(24.49, 2);
    expect(calculateNoReplyRateAfterNewReplies(120, 36, 240)).toBeCloseTo(10, 2);
    expect(result.rows.map((row) => row.newReplies)).toEqual([
      5,
      10,
      20,
      30,
      50,
      100,
      240,
    ]);
    expect(result.rows.map((row) => row.rate)).toEqual([
      expect.closeTo(28.8, 2),
      expect.closeTo(27.6923, 2),
      expect.closeTo(25.7143, 2),
      expect.closeTo(24, 2),
      expect.closeTo(21.1765, 2),
      expect.closeTo(16.3636, 2),
      expect.closeTo(10, 2),
    ]);
  });

  it("rejects invalid service no-reply boundary inputs", () => {
    expect(
      calculateServiceNoReply({
        total: 120,
        missed: 36,
        targetPercent: 0,
      }).invalid,
    ).toBe(true);
    expect(
      calculateServiceNoReply({
        total: 120,
        missed: 36,
        targetPercent: 100,
      }).invalid,
    ).toBe(true);
    expect(
      calculateServiceNoReply({
        total: 120,
        missed: 121,
        targetPercent: 10,
      }).invalid,
    ).toBe(true);
  });
});

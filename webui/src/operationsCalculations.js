function finite(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function percent(value) {
  return Math.max(0, Math.min(100, finite(value))) / 100;
}

export function calculateReviewScenario(input = {}) {
  const total = Math.max(0, Math.floor(finite(input.total)));
  const negative = Math.min(total, Math.max(0, finite(input.negative)));
  const targetRate = percent(input.targetPercent);
  const reviewRate = percent(input.reviewRatePercent);
  const newNegativeRate = percent(input.newNegativeRatePercent);
  const orders = Math.max(0, Math.floor(finite(input.orders)));
  const currentRate = total > 0 ? negative / total : null;
  const rows = [...new Set([orders, 5, 10, 20, 50])].map((value) => {
    const reviews = value * reviewRate;
    const addedNegative = reviews * newNegativeRate;
    return {
      orders: value,
      reviews,
      addedNegative,
      rate:
        total + reviews > 0
          ? ((negative + addedNegative) / (total + reviews)) * 100
          : null,
    };
  });
  let required = null;
  let warning = "";
  if (currentRate !== null && currentRate <= targetRate) {
    warning = "当前差评率已低于目标。";
  } else if (reviewRate === 0) {
    warning = "历史订单转有效主动评价率为 0，新增订单不会自动形成评价。";
  } else if (newNegativeRate >= targetRate) {
    warning = "新增评价差评率不低于目标时，扩大订单规模无法单独拉低差评率。";
  } else {
    required = Math.max(
      0,
      Math.ceil(
        (negative - targetRate * total) /
          (reviewRate * (targetRate - newNegativeRate)),
      ),
    );
  }
  return {
    currentRate,
    targetRate,
    required,
    warning,
    rows,
    formula:
      "未来差评率 =（现有差评 + 新增有效评价 × 新增差评率）÷（现有有效评价 + 新增有效评价）",
  };
}

export function calculateRefundScenario(input = {}) {
  const orders = Math.max(0, Math.floor(finite(input.orders)));
  const refunds = Math.min(orders, Math.max(0, finite(input.refunds)));
  const targetRate = percent(input.targetPercent);
  const newRefundRate = percent(input.newRefundRatePercent);
  const addedOrders = Math.max(0, Math.floor(finite(input.newOrders)));
  const currentRate = orders > 0 ? refunds / orders : null;
  const rows = [...new Set([addedOrders, 5, 10, 20, 50, 100])].map((value) => {
    const addedRefunds = value * newRefundRate;
    const rate =
      orders + value > 0
        ? ((refunds + addedRefunds) / (orders + value)) * 100
        : null;
    return {
      orders: value,
      addedRefunds,
      rate,
      gap: rate === null ? null : rate - targetRate * 100,
    };
  });
  let required = null;
  let warning = "";
  if (currentRate !== null && currentRate <= targetRate) {
    warning = "当前品质退款率已低于目标。";
  } else if (newRefundRate >= targetRate) {
    warning = "新增订单品质退款率不低于目标时，只增加订单无法把比例拉到目标以下。";
  } else {
    required = Math.max(
      0,
      Math.ceil(
        (refunds - targetRate * orders) / (targetRate - newRefundRate),
      ),
    );
  }
  return {
    currentRate,
    targetRate,
    required,
    warning,
    rows,
    formula:
      "未来品质退款率 =（现有品质退款 + 新增已发货订单 × 新增退款率）÷（现有已发货订单 + 新增订单）",
  };
}

export function calculateTrafficRequirement(input = {}) {
  const targetOrders = Math.max(1, Math.ceil(finite(input.targetOrders, 1)));
  const clickRate = Math.max(0.001, percent(input.clickRatePercent));
  const payConversion = Math.max(0.0001, percent(input.payConversionPercent));
  const visits = Math.ceil(targetOrders / payConversion);
  const exposures = Math.ceil(visits / clickRate);
  return {
    visits,
    exposures,
    formula: "新增订单 = 新增曝光 × 商品点击率 × 访客支付转化率",
    rows: [...new Set([targetOrders, 5, 10, 20, 50])].map((orders) => {
      const targetVisits = Math.ceil(orders / payConversion);
      return {
        orders,
        visits: targetVisits,
        exposures: Math.ceil(targetVisits / clickRate),
      };
    }),
  };
}

export function calculateServiceNoReply(input = {}) {
  const total = finite(input.total, Number.NaN);
  const missed = finite(input.missed, Number.NaN);
  const targetPercent = finite(input.targetPercent, Number.NaN);
  const validInputs =
    Number.isFinite(total) &&
    Number.isFinite(missed) &&
    Number.isFinite(targetPercent) &&
    total >= 0 &&
    missed >= 0 &&
    missed <= total &&
    targetPercent >= 0;
  const targetRate = validInputs ? targetPercent / 100 : null;
  const currentRate = validInputs && total > 0 ? missed / total : null;
  let required = null;
  let warning = "";
  let invalid = false;
  if (!validInputs) {
    invalid = true;
    warning = "请输入有效轮次：未回复轮次不能为负且不能超过有效咨询轮次。";
  } else if (total === 0) {
    invalid = true;
    warning = "有效咨询轮次必须大于 0 才能计算不回复率。";
  } else if (targetRate <= 0 || targetRate >= 1) {
    invalid = true;
    warning = "目标未回复率必须大于 0% 且低于 100%，否则目标不可达成或输入无效。";
  } else if (currentRate <= targetRate) {
    required = 0;
    warning = "当前不回复率已达到目标，无需新增回复。";
  } else {
    required = Math.max(0, Math.ceil(missed / targetRate - total));
  }
  const rowCandidates = [5, 10, 20, 30, 50, 100];
  if (required > 0) {
    rowCandidates.push(required);
  }
  const rows = [...new Set(rowCandidates)].map((newReplies) => ({
    newReplies,
    newTotal: validInputs ? total + newReplies : null,
    rate: validInputs
      ? calculateNoReplyRateAfterNewReplies(total, missed, newReplies)
      : null,
  }));
  return {
    currentRate,
    targetRate,
    required,
    rows,
    warning,
    invalid,
    formula:
      "新增回复后不回复率 = 现有未回复轮次 ÷（现有有效咨询轮次 + 新增且已回复咨询轮次）× 100%",
  };
}

export function calculateNoReplyRateAfterNewReplies(
  total,
  missed,
  newReplies,
) {
  const denominator = finite(total) + Math.max(0, finite(newReplies));
  return denominator > 0 ? (finite(missed) / denominator) * 100 : null;
}

export function yuanToCents(value, label) {
  const amount = Number(value);
  if (!Number.isFinite(amount) || amount < 0) {
    throw new Error(`${label}必须是非负数字`);
  }
  return Math.round(amount * 100);
}

export function validateLadderRules(text) {
  for (const raw of String(text || "").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    const parts = line.split("=").map((item) => item.trim());
    if (parts.length !== 4) {
      throw new Error("阶梯倍率每行必须填写：最低原价=最高原价=倍率=加价（元）");
    }
    const [low, high, multiplier] = parts.slice(0, 3).map(Number);
    if (![low, high, multiplier].every(Number.isFinite) || low < 0 || high < low || multiplier <= 0) {
      throw new Error("阶梯倍率的价格区间或倍率无效");
    }
    if (!Number.isFinite(Number(parts[3]))) {
      throw new Error("阶梯倍率加价必须是数字");
    }
  }
  return true;
}

export function validateLicenseGroups(text) {
  const value = String(text || "").trim();
  if (!value) return "";
  const parsed = JSON.parse(value);
  if (!Array.isArray(parsed)) {
    throw new Error("申请证照组必须是 JSON 数组");
  }
  return value;
}

export function buildUploadTemplatePayload({
  form,
  sourceTemplate = {},
  priceOffsetYuan,
  fixedPriceYuan,
}) {
  const name = String(form.name || "").trim();
  if (!name) throw new Error("模板名称不能为空");
  const multiplier = Number(form.price_multiplier);
  if (!Number.isFinite(multiplier) || multiplier < 0) {
    throw new Error("默认价格倍率必须是非负数字");
  }
  const decimals = Number(form.final_price_decimals);
  if (!Number.isInteger(decimals) || decimals < 0 || decimals > 4) {
    throw new Error("最终价格小数位必须是 0–4 的整数");
  }
  const stock = Number(form.stock);
  if (!Number.isInteger(stock) || stock < 0) {
    throw new Error("统一库存必须是非负整数");
  }
  const deposit = Number(form.deposit_limit);
  if (!Number.isInteger(deposit) || deposit < 0) {
    throw new Error("最高保证金必须是非负整数元");
  }
  validateLadderRules(form.ladder_rules);
  const licenseGroups = validateLicenseGroups(form.category_license_groups);
  return {
    ...JSON.parse(JSON.stringify(sourceTemplate)),
    ...JSON.parse(JSON.stringify(form)),
    id: sourceTemplate.id || "",
    name,
    price_multiplier: multiplier,
    price_offset: yuanToCents(priceOffsetYuan, "加价"),
    fixed_price: yuanToCents(fixedPriceYuan, "固定价格"),
    final_price_decimals: decimals,
    stock,
    deposit_limit: deposit,
    low_price_multiplier: Math.max(1, Number(form.low_price_multiplier || 2)),
    trim_head: Math.max(0, Number(form.trim_head || 0)),
    trim_detail: Math.max(0, Number(form.trim_detail || 0)),
    category_license_groups: licenseGroups,
    ladder_rules: String(form.ladder_rules || "").trim(),
    cloud_violation_types: String(form.cloud_violation_types || "").trim(),
  };
}

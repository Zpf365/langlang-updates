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

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value ?? {}));
}

function nonNegativeInteger(value) {
  const number = Math.floor(Number(value || 0));
  return Number.isFinite(number) && number > 0 ? number : 0;
}

function removeOneBasedIndex(images, index) {
  const ordinal = nonNegativeInteger(index);
  if (!ordinal || ordinal > images.length) return images;
  return [...images.slice(0, ordinal - 1), ...images.slice(ordinal)];
}

export function applyImageRules(payload, template = {}) {
  const prepared = cloneJson(payload);
  const rules = {
    remove_head_index: nonNegativeInteger(template.remove_head_index),
    trim_head: nonNegativeInteger(template.trim_head),
    remove_detail_index: nonNegativeInteger(template.remove_detail_index),
    trim_detail: nonNegativeInteger(template.trim_detail),
    trim_detail_tail: nonNegativeInteger(template.trim_detail_tail),
    detail_keep_first: nonNegativeInteger(template.detail_keep_first),
  };
  const descInfo = prepared.desc_info && typeof prepared.desc_info === "object"
    ? prepared.desc_info
    : {};
  const clean = (values) => (Array.isArray(values) ? values : [])
    .map((value) => String(value || "").trim())
    .filter(Boolean);
  let headImages = clean(prepared.head_imgs);
  let detailImages = clean(descInfo.imgs);
  const signature = JSON.stringify(rules);
  if (prepared._template_image_processing === signature) return prepared;

  headImages = removeOneBasedIndex(headImages, rules.remove_head_index);
  if (rules.trim_head) headImages = headImages.slice(rules.trim_head);
  headImages = [...new Set(headImages)];

  detailImages = removeOneBasedIndex(detailImages, rules.remove_detail_index);
  if (rules.trim_detail) detailImages = detailImages.slice(rules.trim_detail);
  if (rules.trim_detail_tail) {
    detailImages = detailImages.slice(
      0,
      Math.max(0, detailImages.length - rules.trim_detail_tail),
    );
  }
  if (rules.detail_keep_first) {
    detailImages = detailImages.slice(0, rules.detail_keep_first);
  }
  detailImages = [...new Set(detailImages)];
  prepared.head_imgs = headImages;
  prepared.desc_info = { ...descInfo, imgs: detailImages };
  prepared._template_image_processing = signature;
  prepared._source_image_counts = {
    head: clean(payload?.head_imgs).length,
    detail: clean(payload?.desc_info?.imgs).length,
  };
  return prepared;
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
    remove_head_index: nonNegativeInteger(form.remove_head_index),
    trim_head: nonNegativeInteger(form.trim_head),
    remove_detail_index: nonNegativeInteger(form.remove_detail_index),
    trim_detail: nonNegativeInteger(form.trim_detail),
    trim_detail_tail: nonNegativeInteger(form.trim_detail_tail),
    detail_keep_first: nonNegativeInteger(form.detail_keep_first),
    category_license_groups: licenseGroups,
    ladder_rules: String(form.ladder_rules || "").trim(),
    cloud_violation_types: String(form.cloud_violation_types || "").trim(),
  };
}

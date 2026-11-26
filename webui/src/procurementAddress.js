const ADDRESS_FIELDS = [
  "user_name",
  "tel_number",
  "province_name",
  "city_name",
  "county_name",
  "street_name",
  "detail_info",
];

function text(value) {
  return String(value || "").trim();
}

function hasAddressValue(value) {
  return Boolean(value && ADDRESS_FIELDS.some((key) => text(value[key])));
}

export function procurementAddressForm(detail = {}, order = {}) {
  const cached = detail?.procurement_address;
  const official = detail?.order?.address_info;
  const current = hasAddressValue(cached)
    ? cached
    : hasAddressValue(official)
      ? official
      : {};
  const orderDetail = detail?.order || {};
  return {
    user_name: text(current.user_name || orderDetail.buyer_name || order.buyer_name),
    tel_number: text(current.tel_number || orderDetail.phone || order.phone),
    province_name: text(current.province_name),
    city_name: text(current.city_name),
    county_name: text(current.county_name),
    street_name: text(current.street_name),
    detail_info: text(
      current.detail_info
      || orderDetail.address
      || order.address
    ),
  };
}

export function procurementAddressText(address = {}) {
  return ADDRESS_FIELDS
    .map((key) => text(address[key]))
    .filter(Boolean)
    .join(" ");
}

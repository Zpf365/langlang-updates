const PLATFORM_LABELS = {
  taobao: "淘宝",
  "1688": "1688",
};

function parseUrl(value) {
  try {
    return new URL(String(value || "").trim());
  } catch {
    return null;
  }
}

export function platformLabel(platform) {
  return PLATFORM_LABELS[platform] || "";
}

export function detectProcurementPlatform(value) {
  const url = parseUrl(value);
  if (!url || !["http:", "https:"].includes(url.protocol)) return "";
  const host = url.hostname.toLowerCase().replace(/\.$/, "");
  if (host === "1688.com" || host.endsWith(".1688.com")) return "1688";
  if (
    host === "taobao.com"
    || host.endsWith(".taobao.com")
    || host === "tmall.com"
    || host.endsWith(".tmall.com")
  ) {
    return "taobao";
  }
  return "";
}

export function sourceProductId(value) {
  const url = parseUrl(value);
  if (!url) return "";
  const platform = detectProcurementPlatform(value);
  if (platform === "1688") {
    const match = url.pathname.match(/\/offer\/(\d{6,})\.html/i);
    if (match) return match[1];
    return url.searchParams.get("offerId")
      || url.searchParams.get("offer_id")
      || "";
  }
  if (platform === "taobao") {
    return url.searchParams.get("id")
      || url.searchParams.get("itemId")
      || "";
  }
  return "";
}

export function normalizedPurchaseUrl(value) {
  const platform = detectProcurementPlatform(value);
  const sourceId = sourceProductId(value);
  if (!platform || !sourceId) return "";
  const url = parseUrl(value);
  if (platform === "1688") {
    return `https://detail.1688.com/offer/${sourceId}.html`;
  }
  const host = url.hostname.toLowerCase().includes("tmall.com")
    ? "detail.tmall.com"
    : "item.taobao.com";
  return `https://${host}/item.htm?id=${sourceId}`;
}

export function accountPlatform(account) {
  const host = String(account?.platform || "").trim().toLowerCase();
  if (host === "1688") return "1688";
  if (host === "淘宝" || host === "taobao") return "taobao";
  return host;
}

export function accountsForLink(url, accounts = []) {
  const platform = detectProcurementPlatform(url);
  if (!platform) return [];
  return (accounts || []).filter(
    (account) => accountPlatform(account) === platform,
  );
}

export function chooseLinkAccount(url, accounts = [], currentId = "") {
  const matching = accountsForLink(url, accounts);
  if (!matching.length) return null;
  const current = matching.find(
    (account) => String(account.id) === String(currentId),
  );
  if (current) return current;
  return matching.find(
    (account) => String(account.status || "") === "已登录",
  ) || matching[0];
}

export function linkValidation(link, accounts = []) {
  const platform = detectProcurementPlatform(link?.url);
  if (!String(link?.url || "").trim()) {
    return { valid: false, platform: "", reason: "请粘贴淘宝或 1688 商品链接" };
  }
  if (!platform || !sourceProductId(link.url)) {
    return {
      valid: false,
      platform: "",
      reason: "链接不是有效的淘宝、天猫或 1688 商品地址",
    };
  }
  const matching = accountsForLink(link.url, accounts);
  if (!matching.length) {
    return {
      valid: false,
      platform,
      reason: `没有对应的${platformLabel(platform)}账号，请先添加并登录`,
    };
  }
  const selected = matching.find(
    (account) => String(account.id) === String(link.account_id),
  );
  if (!selected) {
    return {
      valid: false,
      platform,
      reason: `请选择${platformLabel(platform)}采购账号`,
    };
  }
  if (String(selected.status || "") !== "已登录") {
    return {
      valid: false,
      platform,
      reason: `${platformLabel(platform)}采购账号当前未登录`,
    };
  }
  if (link.snapshot) {
    const snapshotPlatform = accountPlatform({
      platform: link.snapshot.platform,
    });
    const expectedUrl = normalizedPurchaseUrl(link.url);
    const snapshotUrl = normalizedPurchaseUrl(
      link.snapshot.source_url || "",
    );
    if (
      snapshotPlatform !== platform
      || String(link.snapshot.source_product_id || "")
        !== sourceProductId(link.url)
      || (snapshotUrl && expectedUrl && snapshotUrl !== expectedUrl)
    ) {
      return {
        valid: false,
        platform,
        reason: "链接已变化，请重新读取上家商品",
      };
    }
  }
  return { valid: true, platform, reason: "" };
}

export function usableSourceSkus(sourceSkus = []) {
  const seen = new Map();
  for (const sku of sourceSkus || []) {
    const skuId = String(sku?.sku_id || sku?.out_sku_id || "").trim();
    const label = String(sku?.label || "").trim();
    const price = Number(sku?.price || sku?.sale_price || 0);
    const stockValue = sku?.stock ?? sku?.stock_num;
    const stock = stockValue === null || stockValue === undefined
      ? Number.NaN
      : Number(stockValue);
    if (
      !skuId
      || !label
      || label === "默认规格"
      || !Number.isFinite(price)
      || price <= 0
      || !Number.isFinite(stock)
      || stock <= 0
    ) {
      continue;
    }
    const normalized = { ...sku, sku_id: skuId, label, price, stock };
    const previous = seen.get(skuId);
    if (!previous) seen.set(skuId, normalized);
  }
  return [...seen.values()];
}

const NOISE_MARKERS = [
  "sha256",
  "pyinstaller",
  "候选状态",
  "未发布",
  "测试通过",
  "passed",
  "skipped",
  "代码路径已验证",
];

function cleanText(value) {
  return String(value || "")
    .replace(/!\[[^\]]*]\([^)]*\)/g, "")
    .replace(/\[([^\]]+)]\([^)]*\)/g, "$1")
    .replace(/[`*_]/g, "")
    .replace(/^[#>*+\-\s]+/, "")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/[：:；;]+$/, "");
}

function isUseful(value) {
  const text = cleanText(value);
  const lowered = text.toLowerCase();
  return Boolean(text)
    && text.length <= 180
    && !NOISE_MARKERS.some((marker) => lowered.includes(marker));
}

export function normalizeUpdateSummary(value, notes = "", maxPoints = 6) {
  if (Array.isArray(value)) {
    const groups = [];
    let remaining = maxPoints;
    if (value.every((item) => typeof item === "string")) {
      const items = value
        .filter(isUseful)
        .map(cleanText)
        .slice(0, maxPoints);
      return items.length
        ? [{ title: "主要更新", items }]
        : parseNotesSummary(notes, maxPoints);
    }
    for (const group of value) {
      if (!group || typeof group !== "object" || remaining <= 0) continue;
      const items = Array.isArray(group.items)
        ? group.items
          .filter(isUseful)
          .map(cleanText)
          .slice(0, remaining)
        : [];
      if (!items.length) continue;
      groups.push({
        title: cleanText(group.title) || "主要更新",
        items,
      });
      remaining -= items.length;
    }
    if (groups.length) return groups;
  }
  return parseNotesSummary(notes, maxPoints);
}

export function parseNotesSummary(notes, maxPoints = 6) {
  const sections = [];
  let current = null;
  let inCode = false;
  for (const rawLine of String(notes || "").replace(/\r\n/g, "\n").split("\n")) {
    const line = rawLine.trim();
    if (line.startsWith("```")) {
      inCode = !inCode;
      continue;
    }
    if (inCode || !line) continue;
    const heading = line.match(/^#{2,3}\s+(.+)$/);
    if (heading) {
      const title = cleanText(heading[1]);
      current = title && !["边界", "候选", "测试"].some((marker) => title.includes(marker))
        ? { title, items: [] }
        : null;
      if (current) sections.push(current);
      continue;
    }
    const bullet = line.match(/^[-*+]\s+(.+)$/);
    if (bullet && current && isUseful(bullet[1])) {
      current.items.push(cleanText(bullet[1]));
    }
  }
  const groups = [];
  let remaining = maxPoints;
  let itemIndex = 0;
  while (remaining > 0) {
    let added = false;
    for (const section of sections) {
      if (itemIndex >= section.items.length) continue;
      let group = groups.find((item) => item.title === section.title);
      if (!group) {
        group = { title: section.title, items: [] };
        groups.push(group);
      }
      group.items.push(section.items[itemIndex]);
      remaining -= 1;
      added = true;
      if (remaining <= 0) break;
    }
    if (!added) break;
    itemIndex += 1;
  }
  if (groups.length) return groups;
  const fallback = String(notes || "")
    .split("\n")
    .filter(isUseful)
    .map(cleanText)
    .slice(0, maxPoints);
  return fallback.length ? [{ title: "主要更新", items: fallback }] : [];
}

export function updatePointCount(groups) {
  return (groups || []).reduce(
    (total, group) => total + (group.items?.length || 0),
    0,
  );
}

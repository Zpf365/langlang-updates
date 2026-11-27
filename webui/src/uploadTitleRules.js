export function applyUploadTemplateTitleRules(title, template = {}) {
  let value = String(title || "").trim();
  if (template.strip_title_brackets) {
    value = value.replace(/^【[^】]*】\s*/, "");
  }
  for (const line of String(template.title_replace_rules || "").split(/\r?\n/)) {
    const separator = line.indexOf("=");
    if (separator < 0) continue;
    const oldValue = line.slice(0, separator);
    const newValue = line.slice(separator + 1);
    if (oldValue.trim()) {
      value = value.replaceAll(oldValue.trim(), () => newValue.trim());
    }
  }
  return `${template.title_prefix || ""}${value}${template.title_suffix || ""}`.trim();
}

import { call } from "./api";

function legacyCopy(text) {
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.top = "0";
  document.body.appendChild(textarea);
  textarea.select();
  textarea.setSelectionRange(0, textarea.value.length);
  try {
    if (!document.execCommand("copy")) {
      throw new Error("浏览器兼容复制失败");
    }
  } finally {
    textarea.remove();
  }
}

export async function copyText(text, { fallback = true } = {}) {
  const value = String(text ?? "");
  if (!value) throw new Error("没有可复制的内容");
  try {
    await call("clipboard.write", { text: value }, { origin: "clipboard" });
    return "native";
  } catch (nativeError) {
    if (!fallback) throw nativeError;
    try {
      legacyCopy(value);
      return "fallback";
    } catch (fallbackError) {
      throw new Error(
        `原生剪贴板失败：${nativeError instanceof Error ? nativeError.message : String(nativeError)}；`
        + `兼容复制失败：${fallbackError instanceof Error ? fallbackError.message : String(fallbackError)}`,
      );
    }
  }
}

export const APP_ID_REVEAL_TIMEOUT_MS = 60000;

export function maskAppId(value) {
  const appId = String(value || "");
  if (!appId) return "—";
  return `${appId.slice(0, 4)}…`;
}

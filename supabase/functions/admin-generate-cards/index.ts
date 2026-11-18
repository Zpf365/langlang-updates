import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { createHmac, randomBytes } from "node:crypto";
import { jsonResponse } from "../_shared/cors.ts";

const ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";

function createCode(): string {
  const bytes = randomBytes(20);
  const text = Array.from(bytes)
    .map((value) => ALPHABET[value % ALPHABET.length])
    .join("");
  return `LL-${text.slice(0, 5)}-${text.slice(5, 10)}-${text.slice(10, 15)}-${text.slice(15, 20)}`;
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return jsonResponse({});
  if (req.method !== "POST") return jsonResponse({ error: "Method not allowed" }, 405);

  const authorization = req.headers.get("Authorization") ?? "";
  const jwt = authorization.replace(/^Bearer\s+/i, "");
  if (!jwt) return jsonResponse({ error: "缺少登录凭证" }, 401);
  const body = await req.json().catch(() => ({}));
  const count = Math.max(1, Math.min(500, Number(body.count ?? 1)));
  const cardType = body.card_type === "permanent" ? "permanent" : "temporary";
  const durationDays = cardType === "permanent"
    ? null
    : Math.max(1, Math.min(3650, Number(body.duration_days ?? 30)));
  const maxDevices = Math.max(1, Math.min(999, Number(body.max_devices ?? 1)));
  const penaltyHours = Math.max(
    0,
    Math.min(720, Number(body.rebind_penalty_hours ?? 24)),
  );
  const notes = String(body.notes ?? "").slice(0, 500);
  const availableDays = Number(body.available_days ?? 0);

  const admin = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
    { auth: { persistSession: false } },
  );
  const { data: userData, error: userError } = await admin.auth.getUser(jwt);
  if (userError || !userData.user) return jsonResponse({ error: "登录凭证无效" }, 401);
  const { data: profile } = await admin
    .from("profiles")
    .select("role,is_active")
    .eq("id", userData.user.id)
    .maybeSingle();
  if (!profile || profile.role !== "admin" || !profile.is_active) {
    return jsonResponse({ error: "没有卡密生成权限" }, 403);
  }
  const { data: pepperRow, error: pepperError } = await admin
    .from("app_secrets")
    .select("secret_value")
    .eq("secret_key", "card_hash_pepper")
    .maybeSingle();
  if (pepperError) {
    console.error("card pepper lookup failed", pepperError);
    return jsonResponse({ error: "卡密服务读取失败" }, 500);
  }
  const pepper = String(pepperRow?.secret_value ?? "");
  if (!pepper) return jsonResponse({ error: "卡密服务未配置" }, 500);

  const batchId = `batch_${Date.now()}_${randomBytes(4).toString("hex")}`;
  const availableUntil = availableDays > 0
    ? new Date(Date.now() + availableDays * 86_400_000).toISOString()
    : null;
  const rows = [];
  const result = [];
  for (let index = 0; index < count; index += 1) {
    const code = createCode();
    rows.push({
      code_hash: createHmac("sha256", pepper).update(code).digest("hex"),
      code_hint: `****-${code.slice(-5)}`,
      card_type: cardType,
      duration_days: durationDays,
      max_devices: maxDevices,
      rebind_penalty_hours: penaltyHours,
      batch_id: batchId,
      notes,
      available_until: availableUntil,
    });
    result.push(code);
  }
  const { error } = await admin.from("license_cards").insert(rows);
  if (error) return jsonResponse({ error: "卡密保存失败" }, 500);
  return jsonResponse({
    batch_id: batchId,
    card_type: cardType,
    duration_days: durationDays,
    codes: result,
  });
});

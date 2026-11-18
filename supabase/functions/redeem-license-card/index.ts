import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { createHmac } from "node:crypto";
import { jsonResponse } from "../_shared/cors.ts";

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return jsonResponse({});
  if (req.method !== "POST") return jsonResponse({ error: "Method not allowed" }, 405);

  const authorization = req.headers.get("Authorization") ?? "";
  const jwt = authorization.replace(/^Bearer\s+/i, "");
  if (!jwt) return jsonResponse({ error: "缺少登录凭证" }, 401);
  const body = await req.json().catch(() => ({}));
  const code = String(body.code ?? "").trim().toUpperCase();
  const deviceHash = String(body.device_hash ?? "").trim();
  if (!code || !/^[0-9a-f]{64}$/i.test(deviceHash)) {
    return jsonResponse({ error: "卡密或设备标识无效" }, 400);
  }

  const admin = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
    { auth: { persistSession: false } },
  );
  const { data: userData, error: userError } = await admin.auth.getUser(jwt);
  if (userError || !userData.user) {
    return jsonResponse({ error: "登录凭证无效" }, 401);
  }
  const pepper = Deno.env.get("CARD_HASH_PEPPER") ?? "";
  if (!pepper) return jsonResponse({ error: "卡密服务未配置" }, 500);
  const codeHash = createHmac("sha256", pepper).update(code).digest("hex");

  const { data, error } = await admin.rpc("redeem_license_card", {
    p_user_id: userData.user.id,
    p_code_hash: codeHash,
    p_device_hash: deviceHash,
  });
  if (error) return jsonResponse({ error: error.message || "卡密验证失败" }, 409);
  return jsonResponse(data ?? {});
});

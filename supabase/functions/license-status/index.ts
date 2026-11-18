import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { jsonResponse } from "../_shared/cors.ts";

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return jsonResponse({});
  if (req.method !== "GET") return jsonResponse({ error: "Method not allowed" }, 405);

  const authorization = req.headers.get("Authorization") ?? "";
  const jwt = authorization.replace(/^Bearer\s+/i, "");
  if (!jwt) return jsonResponse({ error: "缺少登录凭证" }, 401);

  const admin = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
    { auth: { persistSession: false } },
  );
  const { data: userData, error: userError } = await admin.auth.getUser(jwt);
  if (userError || !userData.user) {
    return jsonResponse({ error: "登录凭证无效" }, 401);
  }
  const { data: profile } = await admin
    .from("profiles")
    .select("*")
    .eq("id", userData.user.id)
    .maybeSingle();
  if (!profile) return jsonResponse({ error: "账户资料不存在" }, 404);
  if (!profile.is_active) return jsonResponse({ error: "账户已被停用" }, 403);

  const { data: devices } = await admin
    .from("devices")
    .select("id")
    .eq("user_id", profile.id)
    .is("revoked_at", null);
  const active = profile.permanent_license ||
    (profile.license_expires_at != null &&
      new Date(profile.license_expires_at).getTime() > Date.now());
  const remaining = profile.permanent_license
    ? 999999
    : Math.max(
      0,
      Math.ceil(
        (new Date(profile.license_expires_at ?? 0).getTime() - Date.now()) /
          86_400_000,
      ),
    );
  return jsonResponse({
    active,
    permanent: profile.permanent_license,
    expires_at: profile.license_expires_at,
    status_text: profile.permanent_license
      ? "永久授权"
      : active
      ? "授权有效"
      : "授权已到期",
    remaining_days: remaining,
    max_devices: profile.max_devices,
    device_bound: (devices?.length ?? 0) > 0,
    server_time: new Date().toISOString(),
  });
});

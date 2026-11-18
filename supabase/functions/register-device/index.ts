import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { jsonResponse } from "../_shared/cors.ts";

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return jsonResponse({});
  if (req.method !== "POST") return jsonResponse({ error: "Method not allowed" }, 405);

  const authorization = req.headers.get("Authorization") ?? "";
  const jwt = authorization.replace(/^Bearer\s+/i, "");
  if (!jwt) return jsonResponse({ error: "缺少登录凭证" }, 401);
  const body = await req.json().catch(() => ({}));
  const deviceHash = String(body.device_hash ?? "").trim();
  const deviceName = String(body.device_name ?? "").trim().slice(0, 80);
  const appVersion = String(body.app_version ?? "").trim().slice(0, 30);
  if (!/^[0-9a-f]{64}$/i.test(deviceHash)) {
    return jsonResponse({ error: "设备标识无效" }, 400);
  }

  const admin = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
    { auth: { persistSession: false } },
  );
  const { data: userData, error: userError } = await admin.auth.getUser(jwt);
  const user = userData.user;
  if (userError || !user) return jsonResponse({ error: "登录凭证无效" }, 401);

  const { data: profile } = await admin
    .from("profiles")
    .select("*")
    .eq("id", user.id)
    .maybeSingle();
  if (!profile) return jsonResponse({ error: "账户资料不存在" }, 404);
  if (!profile.is_active) return jsonResponse({ error: "账户已被停用" }, 403);

  const { data: matching } = await admin
    .from("devices")
    .select("*")
    .eq("user_id", user.id)
    .eq("device_hash", deviceHash)
    .maybeSingle();
  if (matching && !matching.revoked_at) {
    await admin
      .from("devices")
      .update({
        device_name: deviceName,
        app_version: appVersion,
        last_seen_at: new Date().toISOString(),
      })
      .eq("id", matching.id);
    return jsonResponse({ ok: true, rebind_penalty_applied: false });
  }

  const { data: activeDevices } = await admin
    .from("devices")
    .select("*")
    .eq("user_id", user.id)
    .is("revoked_at", null)
    .order("last_seen_at", { ascending: true });

  let penaltyApplied = false;
  if ((activeDevices?.length ?? 0) >= profile.max_devices) {
    const revokeIds = (activeDevices ?? []).map((device) => device.id);
    if (revokeIds.length) {
      await admin
        .from("devices")
        .update({ revoked_at: new Date().toISOString() })
        .in("id", revokeIds);
    }
    if (!profile.permanent_license && profile.license_expires_at) {
      const expires = new Date(profile.license_expires_at);
      const penaltyMs = Math.max(0, profile.rebind_penalty_hours) * 3_600_000;
      const deducted = new Date(expires.getTime() - penaltyMs);
      const now = new Date();
      const nextExpiry = deducted > now ? deducted : now;
      await admin
        .from("profiles")
        .update({ license_expires_at: nextExpiry.toISOString() })
        .eq("id", user.id);
      penaltyApplied = penaltyMs > 0;
    }
  }

  const now = new Date().toISOString();
  const devicePayload = {
    device_name: deviceName,
    app_version: appVersion,
    last_seen_at: now,
  };
  const deviceQuery = matching
    ? admin
        .from("devices")
        .update({
          ...devicePayload,
          bound_at: now,
          revoked_at: null,
        })
        .eq("id", matching.id)
    : admin
        .from("devices")
        .insert({
          user_id: user.id,
          device_hash: deviceHash,
          ...devicePayload,
          bound_at: now,
        });
  const { data: device, error } = await deviceQuery
    .select()
    .single();
  if (error) return jsonResponse({ error: "设备绑定失败" }, 500);

  if (penaltyApplied) {
    await admin.from("license_events").insert({
      user_id: user.id,
      device_id: device.id,
      event_type: "device_rebind_penalty",
      days_delta: 0,
      detail: { penalty_hours: profile.rebind_penalty_hours },
    });
  }
  return jsonResponse({
    ok: true,
    device_id: device.id,
    rebind_penalty_applied: penaltyApplied,
  });
});

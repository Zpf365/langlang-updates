import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { jsonResponse } from "../_shared/cors.ts";

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return jsonResponse({});
  if (req.method !== "GET") {
    return jsonResponse({ error: "Method not allowed" }, 405);
  }
  const jwt = (req.headers.get("Authorization") ?? "")
    .replace(/^Bearer\s+/i, "");
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
    .select("role,is_active")
    .eq("id", userData.user.id)
    .maybeSingle();
  if (!profile?.is_active || profile.role !== "admin") {
    return jsonResponse({ error: "需要管理员权限" }, 403);
  }
  const url = new URL(req.url);
  const limit = Math.min(500, Math.max(1, Number(url.searchParams.get("limit") ?? 100)));
  const { data, error } = await admin
    .from("diagnostic_reports")
    .select("*")
    .order("created_at", { ascending: false })
    .limit(limit);
  if (error) return jsonResponse({ error: error.message }, 500);
  return jsonResponse({ reports: data ?? [] });
});

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import {
  jsonResponse,
  normalizeEmail,
  normalizeUsername,
} from "../_shared/cors.ts";

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return jsonResponse({});
  if (req.method !== "POST") return jsonResponse({ error: "Method not allowed" }, 405);

  const body = await req.json().catch(() => ({}));
  const identifier = String(body.identifier ?? "").trim();
  if (!identifier) return jsonResponse({ error: "请输入账户名或邮箱" }, 400);

  const admin = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
    { auth: { persistSession: false } },
  );
  let query = admin.from("profiles").select("email,is_active");
  query = identifier.includes("@")
    ? query.eq("email", normalizeEmail(identifier))
    : query.eq("username", normalizeUsername(identifier));
  const { data: profile } = await query.maybeSingle();
  if (!profile) {
    return jsonResponse({ error: "如果账户存在，验证码将发送到绑定邮箱" }, 404);
  }
  if (!profile.is_active) {
    return jsonResponse({ error: "账户已被停用，请联系管理员" }, 403);
  }

  const authClient = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_ANON_KEY")!,
    { auth: { persistSession: false, autoRefreshToken: false } },
  );
  const { error } = await authClient.auth.signInWithOtp({
    email: profile.email,
    options: { shouldCreateUser: false },
  });
  if (error) {
    return jsonResponse({ error: "验证码发送失败，请稍后重试" }, 429);
  }
  return jsonResponse({ ok: true, email: profile.email });
});

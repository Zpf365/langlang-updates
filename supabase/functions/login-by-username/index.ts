import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { jsonResponse, normalizeUsername } from "../_shared/cors.ts";

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return jsonResponse({});
  if (req.method !== "POST") return jsonResponse({ error: "Method not allowed" }, 405);

  const body = await req.json().catch(() => ({}));
  const username = normalizeUsername(body.username);
  const password = String(body.password ?? "");
  if (!username || !password) {
    return jsonResponse({ error: "请输入账户名和密码" }, 400);
  }

  const admin = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
    { auth: { persistSession: false } },
  );
  const { data: profile, error: profileError } = await admin
    .from("profiles")
    .select("id,email,username,is_active")
    .eq("username", username)
    .maybeSingle();
  if (profileError) {
    console.error("profile lookup failed", profileError);
    return jsonResponse(
      { error: "暂时无法读取账号资料", code: "SERVER_ERROR" },
      500,
    );
  }
  if (!profile) {
    return jsonResponse(
      { error: "账户名不存在", code: "ACCOUNT_NOT_FOUND" },
      404,
    );
  }
  if (!profile.is_active) {
    return jsonResponse(
      {
        error: "账户已被停用，请联系管理员",
        code: "ACCOUNT_DISABLED",
      },
      403,
    );
  }

  const authClient = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_ANON_KEY")!,
    { auth: { persistSession: false, autoRefreshToken: false } },
  );
  const { data, error } = await authClient.auth.signInWithPassword({
    email: profile.email,
    password,
  });
  if (error || !data.session || !data.user) {
    return jsonResponse(
      { error: "账户名或密码不正确", code: "INVALID_CREDENTIALS" },
      401,
    );
  }
  return jsonResponse({
    ...data.session,
    user: {
      ...data.user,
      user_metadata: {
        ...data.user.user_metadata,
        username: profile.username,
      },
    },
    username: profile.username,
  });
});

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { jsonResponse } from "../_shared/cors.ts";

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return jsonResponse({});
  if (req.method !== "POST") return jsonResponse({ error: "Method not allowed" }, 405);

  const authorization = req.headers.get("Authorization") ?? "";
  const jwt = authorization.replace(/^Bearer\s+/i, "");
  if (!jwt) return jsonResponse({ error: "缺少登录凭证" }, 401);

  const supabase = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
    { auth: { persistSession: false } },
  );
  const { data: userData, error: userError } = await supabase.auth.getUser(jwt);
  const user = userData.user;
  if (userError || !user?.email) {
    return jsonResponse({ error: "登录凭证无效" }, 401);
  }
  const username = String(user.user_metadata?.username ?? "").trim();
  if (!username) {
    return jsonResponse({ error: "账户缺少用户名，请重新注册" }, 400);
  }

  const { data, error } = await supabase.rpc("complete_registration", {
    p_user_id: user.id,
    p_email: user.email,
    p_username: username,
  });
  if (error) {
    return jsonResponse({ error: error.message || "注册信息保存失败" }, 409);
  }
  return jsonResponse({ ok: true, profile: data });
});

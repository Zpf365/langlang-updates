import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import {
  jsonResponse,
  normalizeEmail,
  normalizeUsername,
  validEmail,
  validUsername,
} from "../_shared/cors.ts";

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return jsonResponse({});
  if (req.method !== "POST") return jsonResponse({ error: "Method not allowed" }, 405);

  const body = await req.json().catch(() => ({}));
  const email = normalizeEmail(body.email);
  const username = normalizeUsername(body.username);
  if (!validEmail(email) || !validUsername(username)) {
    return jsonResponse({ error: "邮箱或账户名格式不正确" }, 400);
  }

  const supabase = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
    { auth: { persistSession: false } },
  );

  const { data: profileByEmail } = await supabase
    .from("profiles")
    .select("id")
    .eq("email", email)
    .maybeSingle();
  if (profileByEmail) {
    return jsonResponse({ error: "邮箱已注册" }, 409);
  }

  const { data: profileByName } = await supabase
    .from("profiles")
    .select("id")
    .eq("username", username)
    .maybeSingle();
  if (profileByName) {
    return jsonResponse({ error: "账户名已存在" }, 409);
  }

  const now = new Date();
  const { data: existing } = await supabase
    .from("registration_reservations")
    .select("*")
    .eq("email", email)
    .maybeSingle();
  if (
    existing &&
    new Date(existing.last_sent_at).getTime() > now.getTime() - 60_000
  ) {
    if (String(existing.username).toLowerCase() !== username.toLowerCase()) {
      return jsonResponse({ error: "该邮箱刚刚发起过注册，请稍后重试" }, 429);
    }
    return jsonResponse({ ok: true, reused: true });
  }

  await supabase
    .from("registration_reservations")
    .delete()
    .lt("expires_at", now.toISOString());

  const { error } = await supabase
    .from("registration_reservations")
    .upsert({
      email,
      username,
      expires_at: new Date(now.getTime() + 20 * 60_000).toISOString(),
      last_sent_at: now.toISOString(),
    }, { onConflict: "email" });
  if (error) {
    const message = String(error.message || "");
    if (message.includes("duplicate") || message.includes("unique")) {
      return jsonResponse({ error: "账户名已存在" }, 409);
    }
    return jsonResponse({ error: "无法保留账户名，请稍后重试" }, 500);
  }
  return jsonResponse({ ok: true });
});

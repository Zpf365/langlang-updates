import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { jsonResponse } from "../_shared/cors.ts";

const encoder = new TextEncoder();

function randomToken(bytes = 32): string {
  return btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(bytes))))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
}

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(value));
  return [...new Uint8Array(digest)]
    .map((item) => item.toString(16).padStart(2, "0"))
    .join("");
}

function sessionToken(req: Request): string {
  const value = req.headers.get("x-buyer-session") ?? "";
  return value.trim();
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return jsonResponse({});
  if (req.method !== "POST") return jsonResponse({ error: "Method not allowed" }, 405);
  const admin = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
    { auth: { persistSession: false } },
  );
  const body = await req.json().catch(() => ({}));
  const action = String(body.action ?? "");

  if (action === "exchange_code") {
    const ticket = String(body.ticket ?? "").replace(/^ticket=/, "");
    const code = String(body.code ?? "");
    if (!ticket || !code) return jsonResponse({ error: "绑定票据或登录 code 为空" }, 400);
    const appid = Deno.env.get("WECHAT_BUYER_MINI_APPID") ?? "";
    const secret = Deno.env.get("WECHAT_BUYER_MINI_APPSECRET") ?? "";
    if (!appid || !secret) {
      return jsonResponse(
        {
          error: "小程序服务端 AppID/AppSecret 尚未配置",
          code: "MINI_LOGIN_NOT_CONFIGURED",
        },
        503,
      );
    }
    const ticketHash = await sha256(ticket);
    const { data: pairing, error } = await admin
      .from("buyer_pairing_tickets")
      .select("*")
      .eq("ticket_hash", ticketHash)
      .eq("status", "pending")
      .gt("expires_at", new Date().toISOString())
      .maybeSingle();
    if (error) throw error;
    if (!pairing) return jsonResponse({ error: "绑定票据无效或已使用" }, 409);
    const codeResponse = await fetch(
      "https://api.weixin.qq.com/sns/jscode2session" +
        `?appid=${encodeURIComponent(appid)}` +
        `&secret=${encodeURIComponent(secret)}` +
        `&js_code=${encodeURIComponent(code)}&grant_type=authorization_code`,
    );
    const codeData = await codeResponse.json().catch(() => ({}));
    if (!codeData.openid) {
      return jsonResponse({ error: "微信登录交换失败", detail: codeData }, 502);
    }
    const miniSession = randomToken();
    const sessionHash = await sha256(miniSession);
    const { error: updateError } = await admin
      .from("buyer_pairing_tickets")
      .update({
        status: "claimed",
        claimed_at: new Date().toISOString(),
        mini_session_hash: sessionHash,
        mini_appid: appid,
        mini_openid: String(codeData.openid),
        mini_unionid: codeData.unionid ? String(codeData.unionid) : null,
      })
      .eq("pairing_id", pairing.pairing_id)
      .eq("status", "pending");
    if (updateError) throw updateError;
    return jsonResponse({
      session_token: miniSession,
      pairing_id: pairing.pairing_id,
      identity: {
        appid,
        openid_tail: String(codeData.openid).slice(-6),
        has_unionid: Boolean(codeData.unionid),
      },
      instructions: "请用户本人确认将此微信身份绑定到电脑上的澜琅任务。",
    });
  }

  const token = sessionToken(req);
  if (!token) return jsonResponse({ error: "缺少买家助手会话" }, 401);
  const sessionHash = await sha256(token);
  const { data: pairing, error: sessionError } = await admin
    .from("buyer_pairing_tickets")
    .select("*")
    .eq("mini_session_hash", sessionHash)
    .in("status", ["claimed", "bound"])
    .gt("expires_at", new Date().toISOString())
    .maybeSingle();
  if (sessionError) throw sessionError;
  if (!pairing) return jsonResponse({ error: "买家助手会话已失效" }, 401);

  if (action === "confirm_binding") {
    const appid = String(pairing.mini_appid ?? "");
    const openid = String(pairing.mini_openid ?? "");
    if (!appid || !openid) {
      return jsonResponse({ error: "服务端尚未持有当前微信身份" }, 400);
    }
    const { data: identity, error } = await admin
      .from("buyer_identities")
      .upsert({
        owner_user_id: pairing.owner_user_id,
        mini_appid: appid,
        openid,
        unionid: pairing.mini_unionid ?? null,
        display_name: String(body.display_name ?? "微信买家"),
        identity_status: "verified",
        last_login_at: new Date().toISOString(),
      }, { onConflict: "mini_appid,openid" })
      .select("*")
      .single();
    if (error) throw error;
    await admin
      .from("buyer_device_bindings")
      .insert({
        buyer_identity_id: identity.buyer_identity_id,
        device_label: String(body.device_label ?? "微信"),
      });
    await admin
      .from("buyer_pairing_tickets")
      .update({
        status: "bound",
        bound_at: new Date().toISOString(),
        buyer_identity_id: identity.buyer_identity_id,
        expires_at: new Date(Date.now() + 30 * 86400 * 1000).toISOString(),
      })
      .eq("pairing_id", pairing.pairing_id);
    return jsonResponse({
      buyer_identity_id: identity.buyer_identity_id,
      display_name: identity.display_name,
      bound: true,
    });
  }

  if (!pairing.buyer_identity_id) return jsonResponse({ error: "尚未确认身份绑定" }, 403);
  const identityId = pairing.buyer_identity_id;

  if (action === "list_tasks") {
    const { data, error } = await admin
      .from("buyer_assistant_tasks")
      .select("*,buyer_assistant_task_items(*)")
      .eq("buyer_identity_id", identityId)
      .order("created_at", { ascending: false });
    if (error) throw error;
    return jsonResponse({ tasks: data ?? [] });
  }

  if (action === "item_action") {
    const itemId = String(body.assistant_item_id ?? "");
    const newStatus = String(body.status ?? "");
    const { data: item } = await admin
      .from("buyer_assistant_task_items")
      .select("assistant_task_id")
      .eq("assistant_item_id", itemId)
      .maybeSingle();
    if (!item) return jsonResponse({ error: "商品任务项不存在" }, 404);
    const { data: task } = await admin
      .from("buyer_assistant_tasks")
      .select("buyer_identity_id")
      .eq("assistant_task_id", item.assistant_task_id)
      .maybeSingle();
    if (!task || task.buyer_identity_id !== identityId) {
      return jsonResponse({ error: "任务身份不匹配" }, 403);
    }
    const { error } = await admin
      .from("buyer_assistant_task_items")
      .update({
        status: newStatus,
        user_confirmed_at: newStatus === "user_confirmed"
          ? new Date().toISOString()
          : null,
      })
      .eq("assistant_item_id", itemId);
    if (error) throw error;
    await admin.from("buyer_action_events").insert({
      assistant_task_id: item.assistant_task_id,
      assistant_item_id: itemId,
      buyer_identity_id: identityId,
      action: newStatus,
      result: "info",
      detail_json: {},
    });
    return jsonResponse({
      saved: true,
      note: "用户确认不等于平台购物车服务端核验",
    });
  }

  if (action === "task_action") {
    const taskId = String(body.assistant_task_id ?? "");
    const newStatus = String(body.status ?? "");
    const allowed = new Set(["running", "paused", "cancelled", "awaiting_order"]);
    if (!allowed.has(newStatus)) return jsonResponse({ error: "任务状态不支持" }, 400);
    const { data, error } = await admin
      .from("buyer_assistant_tasks")
      .update({ status: newStatus, updated_at: new Date().toISOString() })
      .eq("assistant_task_id", taskId)
      .eq("buyer_identity_id", identityId)
      .select("assistant_task_id,status")
      .maybeSingle();
    if (error) throw error;
    if (!data) return jsonResponse({ error: "任务不存在或身份不匹配" }, 404);
    return jsonResponse(data);
  }

  if (action === "history") {
    const { data, error } = await admin
      .from("buyer_action_events")
      .select("*")
      .eq("buyer_identity_id", identityId)
      .order("created_at", { ascending: false })
      .limit(200);
    if (error) throw error;
    return jsonResponse({ events: data ?? [] });
  }

  if (action === "orders") {
    const { data, error } = await admin
      .from("buyer_order_matches")
      .select("*")
      .eq("buyer_identity_id", identityId)
      .order("verified_at", { ascending: false });
    if (error) throw error;
    return jsonResponse({ orders: data ?? [] });
  }

  if (action === "unbind") {
    await admin
      .from("buyer_device_bindings")
      .update({ status: "revoked", revoked_at: new Date().toISOString() })
      .eq("buyer_identity_id", identityId);
    await admin
      .from("buyer_identities")
      .update({ identity_status: "revoked" })
      .eq("buyer_identity_id", identityId);
    await admin
      .from("buyer_pairing_tickets")
      .update({ status: "cancelled", mini_session_hash: null })
      .eq("pairing_id", pairing.pairing_id);
    return jsonResponse({ unbound: true });
  }

  return jsonResponse({ error: "未知小程序动作" }, 400);
});

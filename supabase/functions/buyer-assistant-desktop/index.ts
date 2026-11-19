import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { jsonResponse } from "../_shared/cors.ts";

const encoder = new TextEncoder();

function randomToken(bytes = 32): string {
  const raw = crypto.getRandomValues(new Uint8Array(bytes));
  return btoa(String.fromCharCode(...raw))
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

async function miniAccessToken(): Promise<string> {
  const appid = Deno.env.get("WECHAT_BUYER_MINI_APPID") ?? "";
  const secret = Deno.env.get("WECHAT_BUYER_MINI_APPSECRET") ?? "";
  if (!appid || !secret) return "";
  const response = await fetch(
    "https://api.weixin.qq.com/cgi-bin/stable_token",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        grant_type: "client_credential",
        appid,
        secret,
      }),
    },
  );
  const payload = await response.json().catch(() => ({}));
  return String(payload.access_token ?? "");
}

async function pairingCode(ticket: string): Promise<string> {
  const token = await miniAccessToken();
  if (!token) return "";
  const response = await fetch(
    `https://api.weixin.qq.com/wxa/getwxacodeunlimit?access_token=${
      encodeURIComponent(token)
    }`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scene: `ticket=${ticket}`,
        page: "pages/bind/index",
        check_path: false,
        env_version: Deno.env.get("WECHAT_BUYER_MINI_ENV") ?? "release",
      }),
    },
  );
  if (!response.ok) return "";
  const buffer = new Uint8Array(await response.arrayBuffer());
  return btoa(String.fromCharCode(...buffer));
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return jsonResponse({});
  if (req.method !== "POST") return jsonResponse({ error: "Method not allowed" }, 405);
  const auth = req.headers.get("authorization") ?? "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7) : "";
  if (!token) return jsonResponse({ error: "需要登录" }, 401);
  const admin = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
    { auth: { persistSession: false } },
  );
  const { data: userData, error: userError } = await admin.auth.getUser(token);
  if (userError || !userData.user) return jsonResponse({ error: "登录已失效" }, 401);
  const ownerId = userData.user.id;
  const body = await req.json().catch(() => ({}));
  const action = String(body.action ?? "");

  try {
    if (action === "create_pairing") {
      const ticket = randomToken();
      const ticketHash = await sha256(ticket);
      const expiresAt = new Date(Date.now() + 5 * 60 * 1000).toISOString();
      const { data, error } = await admin
        .from("buyer_pairing_tickets")
        .insert({
          owner_user_id: ownerId,
          store_app_id: String(body.store_app_id ?? ""),
          ticket_hash: ticketHash,
          expires_at: expiresAt,
        })
        .select("pairing_id,expires_at")
        .single();
      if (error) throw error;
      const qr = await pairingCode(ticket).catch(() => "");
      return jsonResponse({
        pairing_id: data.pairing_id,
        ticket,
        expires_at: data.expires_at,
        qr_image_base64: qr,
        qr_available: Boolean(qr),
        message: qr
          ? "请使用微信扫描小程序码并确认绑定"
          : "小程序码生成配置未完成；可把绑定票据安全地交给小程序调试环境",
      });
    }

    if (action === "pairing_status") {
      const { data, error } = await admin
        .from("buyer_pairing_tickets")
        .select("status,expires_at,bound_at,buyer_identity_id")
        .eq("pairing_id", String(body.pairing_id ?? ""))
        .eq("owner_user_id", ownerId)
        .maybeSingle();
      if (error) throw error;
      if (!data) return jsonResponse({ error: "绑定任务不存在" }, 404);
      let identity = null;
      if (data.buyer_identity_id) {
        const result = await admin
          .from("buyer_identities")
          .select("buyer_identity_id,display_name,unionid,created_at,last_login_at")
          .eq("buyer_identity_id", data.buyer_identity_id)
          .eq("owner_user_id", ownerId)
          .maybeSingle();
        identity = result.data;
      }
      return jsonResponse({ ...data, identity });
    }

    if (action === "create_task") {
      const task = body.task ?? {};
      const items = Array.isArray(task.items) ? task.items.slice(0, 10) : [];
      if (!items.length) return jsonResponse({ error: "商品任务为空" }, 400);
      const idempotencyKey = String(task.idempotency_key ?? "");
      if (!idempotencyKey) return jsonResponse({ error: "缺少幂等键" }, 400);
      const { data: existing } = await admin
        .from("buyer_assistant_tasks")
        .select("assistant_task_id,status")
        .eq("owner_user_id", ownerId)
        .eq("idempotency_key", idempotencyKey)
        .maybeSingle();
      if (existing) return jsonResponse(existing);
      const { data, error } = await admin
        .from("buyer_assistant_tasks")
        .insert({
          owner_user_id: ownerId,
          buyer_identity_id: task.buyer_identity_id ?? null,
          store_app_id: String(task.store_app_id ?? ""),
          store_name: String(task.store_name ?? ""),
          local_run_id: String(task.local_run_id ?? ""),
          idempotency_key: idempotencyKey,
          requested_count: Math.min(10, Math.max(1, Number(task.requested_count) || 1)),
        })
        .select("assistant_task_id,status")
        .single();
      if (error) throw error;
      const rows = items.map((item: Record<string, unknown>, index: number) => ({
        assistant_task_id: data.assistant_task_id,
        sequence_no: index + 1,
        product_id: String(item.product_id ?? ""),
        sku_id: String(item.sku_id ?? ""),
        title: String(item.title ?? ""),
        image_url: String(item.image_url ?? ""),
        specs_json: item.specs ?? {},
        unit_price_cents: Number(item.unit_price_cents) || 0,
        quantity: Number(item.quantity) || 1,
        stock: Number(item.stock) || 0,
      }));
      const { error: itemError } = await admin
        .from("buyer_assistant_task_items")
        .insert(rows);
      if (itemError) {
        await admin
          .from("buyer_assistant_tasks")
          .delete()
          .eq("assistant_task_id", data.assistant_task_id);
        throw itemError;
      }
      return jsonResponse(data);
    }

    if (action === "save_integration_state") {
      const { error } = await admin.from("buyer_integration_state").upsert({
        owner_user_id: ownerId,
        store_app_id: String(body.store_app_id ?? ""),
        store_name: String(body.store_name ?? ""),
        open_platform_linked: Boolean(body.open_platform_linked),
        shop_partner_authorized: Boolean(body.shop_partner_authorized),
        product_component_ready: Boolean(body.product_component_ready),
        order_component_ready: Boolean(body.order_component_ready),
        status_reason: String(body.status_reason ?? ""),
        updated_at: new Date().toISOString(),
      });
      if (error) throw error;
      return jsonResponse({ saved: true });
    }

    if (action === "task_status") {
      const { data, error } = await admin
        .from("buyer_assistant_tasks")
        .select("*,buyer_assistant_task_items(*),buyer_action_events(*)")
        .eq("assistant_task_id", String(body.assistant_task_id ?? ""))
        .eq("owner_user_id", ownerId)
        .maybeSingle();
      if (error) throw error;
      if (!data) return jsonResponse({ error: "手机任务不存在" }, 404);
      return jsonResponse(data);
    }

    return jsonResponse({ error: "未知桌面动作" }, 400);
  } catch (error) {
    console.error("buyer assistant desktop failed", error);
    return jsonResponse(
      { error: "买家助手桌面操作失败", detail: String(error) },
      500,
    );
  }
});

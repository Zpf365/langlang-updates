import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Content-Type": "application/json",
};

const PERMISSION_KEYS = new Set([
  "stores_view",
  "stores_edit",
  "stores_delete",
  "stores_login",
  "stores_export",
  "products_view",
  "products_edit",
  "products_upload",
  "orders_view",
  "orders_edit",
  "after_sales",
  "procurement",
  "activities",
  "team_manage",
]);
const ROLES = new Set(["owner", "admin", "manager", "operator", "viewer"]);
const USERNAME_PATTERN = /^[\p{L}\p{N}_:.-]{2,40}$/u;

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: corsHeaders,
  });
}

function normalizePermissions(value: unknown): Record<string, boolean> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .filter(
        ([key, enabled]) =>
          PERMISSION_KEYS.has(key) && typeof enabled === "boolean",
      )
      .map(([key, enabled]) => [key, Boolean(enabled)]),
  );
}

function normalizeGrants(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return [...new Set(
    value
      .map((item) => String(item || "").trim())
      .filter(Boolean),
  )];
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return response({});
  if (req.method !== "POST") return response({ error: "Method not allowed" }, 405);

  const authorization = req.headers.get("Authorization") ?? "";
  const jwt = authorization.replace(/^Bearer\s+/i, "");
  if (!jwt) return response({ error: "缺少登录凭证" }, 401);

  const url = Deno.env.get("SUPABASE_URL")!;
  const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
  const admin = createClient(url, serviceKey, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
  const { data: userData, error: userError } = await admin.auth.getUser(jwt);
  const user = userData.user;
  if (userError || !user) return response({ error: "登录凭证无效" }, 401);

  const { data: caller, error: callerError } = await admin
    .from("organization_members")
    .select("*")
    .eq("user_id", user.id)
    .eq("is_active", true)
    .maybeSingle();
  if (callerError) return response({ error: callerError.message }, 500);
  if (!caller) return response({ error: "当前账户没有组织权限" }, 403);

  const isManager = ["owner", "admin"].includes(caller.role);
  if (!isManager) return response({ error: "只有主账号或管理员可以管理子账号" }, 403);

  let payload: Record<string, unknown>;
  try {
    payload = await req.json();
  } catch {
    return response({ error: "请求内容不是有效 JSON" }, 400);
  }
  const action = String(payload.action || "");

  const audit = async (name: string, target = "", detail: Record<string, unknown> = {}) => {
    await admin.from("organization_audit_logs").insert({
      organization_id: caller.organization_id,
      actor_user_id: user.id,
      action: name,
      target,
      detail,
    });
  };

  if (action === "list") {
    const { data, error } = await admin
      .from("organization_members")
      .select("id,user_id,role,permissions,is_active,created_at,profiles!organization_members_user_id_fkey!inner(username,email)")
      .eq("organization_id", caller.organization_id)
      .order("created_at");
    if (error) return response({ error: error.message }, 500);
    const members = [];
    for (const member of data || []) {
      const { data: grants } = await admin
        .from("organization_store_grants")
        .select("app_id")
        .eq("member_id", member.id);
      const profile = Array.isArray(member.profiles)
        ? member.profiles[0]
        : member.profiles;
      members.push({
        member_id: member.id,
        user_id: member.user_id,
        username: profile?.username || "",
        email: profile?.email || "",
        role: member.role,
        permissions: member.permissions || {},
        is_active: member.is_active,
        grants: (grants || []).map((item) => item.app_id),
      });
    }
    return response({ members });
  }

  if (action === "create_subaccount") {
    const username = String(payload.username || "").trim();
    const password = String(payload.password || "");
    const role = String(payload.role || "operator");
    const permissions = normalizePermissions(payload.permissions);
    const grants = normalizeGrants(payload.grants);
    if (!USERNAME_PATTERN.test(username)) {
      return response({ error: "子账号必须为2至40位中英文、数字或 _ : . -" }, 400);
    }
    if (password.length < 8) return response({ error: "子账号密码至少8位" }, 400);
    if (!ROLES.has(role) || role === "owner" && caller.role !== "owner") {
      return response({ error: "无权创建该角色" }, 403);
    }
    const { data: existing } = await admin
      .from("profiles")
      .select("id")
      .eq("username", username)
      .maybeSingle();
    if (existing) return response({ error: "账号名已存在" }, 409);

    const { data: ownerProfile, error: ownerError } = await admin
      .from("profiles")
      .select("trial_started_at,license_expires_at,permanent_license,max_devices,rebind_penalty_hours")
      .eq("id", caller.organization_id && (
        await admin
          .from("organizations")
          .select("owner_id")
          .eq("id", caller.organization_id)
          .single()
      ).data?.owner_id)
      .single();
    if (ownerError || !ownerProfile) {
      return response({ error: "无法读取主账号授权信息" }, 500);
    }

    const internalEmail = `sub-${crypto.randomUUID()}@subaccounts.langlang.app`;
    const { data: created, error: createError } = await admin.auth.admin.createUser({
      email: internalEmail,
      password,
      email_confirm: true,
      user_metadata: {
        username,
        organization_id: caller.organization_id,
        member_role: role,
      },
    });
    if (createError || !created.user) {
      return response({ error: createError?.message || "创建登录账号失败" }, 500);
    }

    try {
      const { error: profileError } = await admin.from("profiles").insert({
        id: created.user.id,
        email: internalEmail,
        username,
        role: "user",
        is_active: true,
        trial_started_at: ownerProfile.trial_started_at,
        license_expires_at: ownerProfile.license_expires_at,
        permanent_license: ownerProfile.permanent_license,
        max_devices: ownerProfile.max_devices,
        rebind_penalty_hours: ownerProfile.rebind_penalty_hours,
      });
      if (profileError) throw profileError;
      const { data: membership, error: memberError } = await admin
        .from("organization_members")
        .insert({
          organization_id: caller.organization_id,
          user_id: created.user.id,
          role,
          permissions,
          is_active: true,
          created_by: user.id,
        })
        .select("*")
        .single();
      if (memberError) throw memberError;
      if (grants.length) {
        const { error: grantError } = await admin
          .from("organization_store_grants")
          .insert(
            grants.map((appId) => ({
              organization_id: caller.organization_id,
              member_id: membership.id,
              app_id: appId,
              granted_by: user.id,
            })),
          );
        if (grantError) throw grantError;
      }
      await audit("member_create", username, { role, grants });
      return response({
        member: {
          member_id: membership.id,
          user_id: created.user.id,
          username,
          role,
          permissions,
          is_active: true,
          grants,
        },
      });
    } catch (error) {
      await admin.auth.admin.deleteUser(created.user.id);
      return response({ error: String(error?.message || error) }, 500);
    }
  }

  if (action === "add_existing_account") {
    const username = String(payload.username || "").trim();
    const role = String(payload.role || "admin");
    if (caller.role !== "owner") {
      return response({ error: "只有主账号可以添加共同主账号" }, 403);
    }
    if (!ROLES.has(role)) return response({ error: "角色无效" }, 400);
    const { data: profile } = await admin
      .from("profiles")
      .select("id,username")
      .eq("username", username)
      .maybeSingle();
    if (!profile) return response({ error: "目标主账号尚未注册" }, 404);
    const { data: existingMember } = await admin
      .from("organization_members")
      .select("*")
      .eq("user_id", profile.id)
      .maybeSingle();
    if (existingMember?.organization_id === caller.organization_id) {
      const { error: updateError } = await admin
        .from("organization_members")
        .update({
          role,
          permissions: role === "owner" || role === "admin" ? { all: true } : {},
          is_active: true,
        })
        .eq("id", existingMember.id);
      if (updateError) return response({ error: updateError.message }, 500);
      await audit("member_add_existing", username, { role });
      return response({ member: existingMember });
    }
    if (existingMember) {
      const { data: ownVault } = await admin
        .from("organization_vaults")
        .select("payload_protected")
        .eq("organization_id", existingMember.organization_id)
        .maybeSingle();
      if (ownVault?.payload_protected) {
        return response({
          error: "该账号已有自己的店铺数据，不能直接加入其他团队；请先导出并清空原组织",
        }, 409);
      }
      await admin
        .from("organization_store_grants")
        .delete()
        .eq("member_id", existingMember.id);
      await admin
        .from("organization_members")
        .delete()
        .eq("id", existingMember.id);
      await admin
        .from("organizations")
        .delete()
        .eq("id", existingMember.organization_id);
    }
    const { data: membership, error } = await admin
      .from("organization_members")
      .insert({
        organization_id: caller.organization_id,
        user_id: profile.id,
        role,
        permissions: role === "owner" || role === "admin" ? { all: true } : {},
        is_active: true,
        created_by: user.id,
      })
      .select("*")
      .single();
    if (error) return response({ error: error.message }, 409);
    await audit("member_add_existing", username, { role });
    return response({ member: membership });
  }

  if (action === "reset_password") {
    const memberId = String(payload.member_id || "");
    const password = String(payload.password || "");
    if (password.length < 8) return response({ error: "新密码至少8位" }, 400);
    const { data: target } = await admin
      .from("organization_members")
      .select("*,profiles!organization_members_user_id_fkey!inner(username)")
      .eq("id", memberId)
      .eq("organization_id", caller.organization_id)
      .maybeSingle();
    if (!target) return response({ error: "成员不存在" }, 404);
    if (target.role === "owner" && caller.role !== "owner") {
      return response({ error: "管理员不能重置主账号密码" }, 403);
    }
    const profile = Array.isArray(target.profiles) ? target.profiles[0] : target.profiles;
    const { error } = await admin.auth.admin.updateUserById(
      target.user_id,
      { password },
    );
    if (error) return response({ error: error.message }, 500);
    await audit("member_password_reset", profile?.username || target.user_id);
    return response({ ok: true });
  }

  if (action === "update_member") {
    const memberId = String(payload.member_id || "");
    const role = String(payload.role || "operator");
    const permissions = normalizePermissions(payload.permissions);
    const grants = normalizeGrants(payload.grants);
    const isActive = payload.is_active !== false;
    if (!ROLES.has(role)) return response({ error: "角色无效" }, 400);
    if (role === "owner" && caller.role !== "owner") {
      return response({ error: "只有主账号可以设置共同主账号" }, 403);
    }
    const { data: target } = await admin
      .from("organization_members")
      .select("*,profiles!organization_members_user_id_fkey!inner(username)")
      .eq("id", memberId)
      .eq("organization_id", caller.organization_id)
      .maybeSingle();
    if (!target) return response({ error: "成员不存在" }, 404);
    const { data: organization, error: organizationError } = await admin
      .from("organizations")
      .select("owner_id")
      .eq("id", caller.organization_id)
      .single();
    if (organizationError || !organization) {
      return response({ error: "无法读取团队主账号信息" }, 500);
    }
    if (target.user_id === organization.owner_id) {
      if (role !== "owner" || !isActive) {
        return response({ error: "不能降级或停用团队主账号" }, 403);
      }
    } else if (target.role === "owner") {
      if (caller.role !== "owner") {
        return response({ error: "只有主账号可以修改共同主账号" }, 403);
      }
      if (!isActive) {
        const { count, error: countError } = await admin
          .from("organization_members")
          .select("*", { count: "exact", head: true })
          .eq("organization_id", caller.organization_id)
          .eq("role", "owner")
          .eq("is_active", true);
        if (countError) return response({ error: countError.message }, 500);
        if ((count || 0) <= 1) {
          return response({ error: "团队至少需要保留一个启用中的主账号" }, 409);
        }
      }
    }
    if (target.user_id === user.id && !isActive) {
      return response({ error: "不能停用自己" }, 400);
    }
    const { error: updateError } = await admin
      .from("organization_members")
      .update({
        role,
        permissions,
        is_active: isActive,
      })
      .eq("id", memberId);
    if (updateError) return response({ error: updateError.message }, 500);
    await admin
      .from("profiles")
      .update({ is_active: isActive })
      .eq("id", target.user_id);
    await admin
      .from("organization_store_grants")
      .delete()
      .eq("member_id", memberId);
    if (grants.length) {
      const { error: grantsError } = await admin
        .from("organization_store_grants")
        .insert(
          grants.map((appId) => ({
            organization_id: caller.organization_id,
            member_id: memberId,
            app_id: appId,
            granted_by: user.id,
          })),
        );
      if (grantsError) return response({ error: grantsError.message }, 500);
    }
    await audit(
      isActive ? "member_update" : "member_disable",
      (Array.isArray(target.profiles) ? target.profiles[0] : target.profiles)?.username || memberId,
      { role, permissions, grants, is_active: isActive },
    );
    return response({ ok: true });
  }

  return response({ error: "不支持的操作" }, 400);
});

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { jsonResponse } from "../_shared/cors.ts";

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return jsonResponse({});
  if (req.method !== "POST") {
    return jsonResponse({ error: "Method not allowed" }, 405);
  }
  const jwt = (req.headers.get("Authorization") ?? "")
    .replace(/^Bearer\s+/i, "");
  const admin = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
    { auth: { persistSession: false } },
  );
  let userId: string | null = null;
  if (jwt) {
    const { data: userData, error: userError } = await admin.auth.getUser(jwt);
    if (userError || !userData.user) {
      return jsonResponse({ error: "登录凭证无效" }, 401);
    }
    userId = userData.user.id;
  } else {
    const testerKey = req.headers.get("X-LangLang-Tester-Key") ?? "";
    const expected = Deno.env.get("TESTER_DIAGNOSTIC_KEY") ?? "";
    if (!expected || testerKey !== expected) {
      return jsonResponse({ error: "缺少测试诊断密钥" }, 401);
    }
  }
  const body = await req.json();
  const report = body?.report;
  if (!report || typeof report !== "object") {
    return jsonResponse({ error: "缺少诊断内容" }, 400);
  }
  const reportId = String(report.report_id ?? "").slice(0, 80);
  if (!reportId) return jsonResponse({ error: "缺少 report_id" }, 400);
  const { error } = await admin.from("diagnostic_reports").upsert(
    {
      report_id: reportId,
      user_id: userId,
      username: String(report.username ?? "").slice(0, 100),
      app_version: String(report.version ?? "").slice(0, 30),
      device_hash: String(report.device_hash ?? "").slice(0, 100),
      category: String(report.category ?? "").slice(0, 100),
      severity: String(report.severity ?? "error").slice(0, 30),
      route: String(report.route ?? "").slice(0, 200),
      page: String(report.page ?? "").slice(0, 100),
      message: String(report.message ?? "").slice(0, 8000),
      detail: report.detail ?? {},
      process_info: report.process ?? {},
      created_at: report.created_at ?? new Date().toISOString(),
    },
    { onConflict: "report_id" },
  );
  if (error) return jsonResponse({ error: error.message }, 500);
  return jsonResponse({ accepted: true, report_id: reportId });
});

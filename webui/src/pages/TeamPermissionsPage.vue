<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { call } from "../api";
import Icon from "../components/Icon.vue";
import ModalDialog from "../components/ModalDialog.vue";
import { ensureBootstrap, sessionState } from "../session";

const router = useRouter();
const loading = ref(true);
const error = ref("");
const members = ref([]);
const audit = ref([]);
const search = ref("");
const roleFilter = ref("");
const accessFilter = ref("all");
const selected = ref(new Set());
const feedback = reactive({ message: "", tone: "" });
const drawer = reactive({
  open: false,
  member: null,
  permissions: {},
  busy: false,
});
const modal = reactive({
  open: false,
  title: "",
  subtitle: "",
  busy: false,
  action: null,
});
const createForm = reactive({
  username: "",
  password: "",
  role: "operator",
  permissions: {
    operations_view: false,
    operations_task_manage: false,
    operations_export: false,
    operations_rule_manage: false,
    operations_open_backend: false,
  },
});
const existingOpen = ref(false);
const existingBusy = ref(false);
const existingForm = reactive({
  username: "",
  role: "admin",
});

const permissionOptions = [
  ["operations_view", "查看运营中心", "访问总览、店铺健康、商品机会和运营打法"],
  ["operations_task_manage", "管理行动任务", "新建、完成、恢复和延期运营任务"],
  ["operations_export", "导出运营数据", "导出店铺诊断、商品机会和行动记录"],
  ["operations_rule_manage", "修改巡检规则", "修改本地告警阈值和巡检规则"],
  ["operations_open_backend", "打开官方后台", "打开并校验指定店铺和商品的官方页面"],
];
const operationTags = [
  ["operations_view", "查看"],
  ["operations_task_manage", "任务"],
  ["operations_export", "导出"],
  ["operations_rule_manage", "规则"],
  ["operations_open_backend", "后台"],
];

const filteredMembers = computed(() => {
  const term = search.value.trim().toLowerCase();
  return members.value.filter((member) => {
    const text = `${member.username || ""} ${member.display_name || ""} ${member.member_id || ""}`.toLowerCase();
    const access = Boolean(member.permissions?.operations_view);
    return (
      (!term || text.includes(term)) &&
      (!roleFilter.value || member.role === roleFilter.value) &&
      (accessFilter.value === "all" ||
        (accessFilter.value === "enabled" ? access : !access))
    );
  });
});
const selectedMembers = computed(() =>
  filteredMembers.value.filter((member) => selected.value.has(member.member_id)),
);
const stats = computed(() => ({
  total: members.value.length,
  access: members.value.filter((member) => member.permissions?.operations_view).length,
  backend: members.value.filter((member) => member.permissions?.operations_open_backend).length,
}));

function showFeedback(message, tone = "success") {
  feedback.message = message;
  feedback.tone = tone;
  window.setTimeout(() => {
    if (feedback.message === message) feedback.message = "";
  }, 3200);
}

function roleLabel(role) {
  return {
    owner: "主账号",
    admin: "共同主账号",
    manager: "管理员",
    operator: "运营员",
    viewer: "只读",
  }[role] || role || "未知";
}

function displayName(member) {
  return (
    member.display_name ||
    member.username ||
    member.email ||
    member.member_id
  );
}

function operationPermissionPayload(member, values = member.permissions) {
  const view = Boolean(values?.operations_view);
  return {
    operations_view: view,
    operations_task_manage: view && Boolean(values?.operations_task_manage),
    operations_export: view && Boolean(values?.operations_export),
    operations_rule_manage: view && Boolean(values?.operations_rule_manage),
    operations_open_backend: view && Boolean(values?.operations_open_backend),
  };
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    await ensureBootstrap({ force: true });
    const payload = await call("team.operations.members");
    members.value = payload.members || [];
    audit.value = payload.audit || [];
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loading.value = false;
  }
}

function toggleAll(event) {
  if (event.target.checked) {
    selected.value = new Set(
      filteredMembers.value
        .filter((member) => member.role !== "owner")
        .map((member) => member.member_id),
    );
  } else {
    selected.value = new Set();
  }
}

function toggleOne(member) {
  const next = new Set(selected.value);
  if (next.has(member.member_id)) next.delete(member.member_id);
  else next.add(member.member_id);
  selected.value = next;
}

function openDrawer(member) {
  drawer.open = true;
  drawer.member = member;
  drawer.permissions = operationPermissionPayload(member);
  drawer.busy = false;
}

function closeDrawer() {
  if (drawer.busy) return;
  drawer.open = false;
  drawer.member = null;
}

async function saveDrawer() {
  if (!drawer.member || drawer.busy) return;
  if (drawer.member.role === "owner") {
    closeDrawer();
    return;
  }
  drawer.busy = true;
  try {
    await call("team.operations.save", {
      member_id: drawer.member.member_id,
      permissions: operationPermissionPayload(drawer.member, drawer.permissions),
    });
    drawer.open = false;
    await load();
    showFeedback("成员运营权限已保存，下一次请求立即生效");
  } catch (cause) {
    showFeedback(cause instanceof Error ? cause.message : String(cause), "error");
  } finally {
    drawer.busy = false;
  }
}

function openExistingAccount() {
  existingForm.username = "";
  existingForm.role = "admin";
  existingOpen.value = true;
}

async function addExistingAccount() {
  if (existingBusy.value || !existingForm.username.trim()) return;
  existingBusy.value = true;
  try {
    await call("team.operations.add-existing", {
      username: existingForm.username.trim(),
      role: existingForm.role,
    });
    existingOpen.value = false;
    await load();
    showFeedback("已有账号已加入当前团队");
  } catch (cause) {
    showFeedback(cause instanceof Error ? cause.message : String(cause), "error");
  } finally {
    existingBusy.value = false;
  }
}

function openBatch(enabled) {
  const rows = selectedMembers.value.filter((member) => member.role !== "owner");
  if (!rows.length) {
    showFeedback("请先选择至少一名非主账号成员", "error");
    return;
  }
  modal.open = true;
  modal.title = enabled ? "批量开启运营中心" : "批量关闭运营中心";
  modal.subtitle = `将影响 ${rows.length} 名成员，保存后下一次请求立即生效`;
  modal.busy = false;
  modal.action = async () => {
    const permissions = {
      operations_view: enabled,
      operations_task_manage: enabled,
      operations_export: enabled,
      operations_rule_manage: enabled,
      operations_open_backend: enabled,
    };
    await call("team.operations.batch", {
      member_ids: rows.map((member) => member.member_id),
      permissions,
    });
    selected.value = new Set();
    modal.open = false;
    await load();
    showFeedback(`已更新 ${rows.length} 名成员的运营中心总开关`);
  };
}

async function confirmModal() {
  if (!modal.action || modal.busy) return;
  modal.busy = true;
  try {
    await modal.action();
  } catch (cause) {
    showFeedback(cause instanceof Error ? cause.message : String(cause), "error");
  } finally {
    modal.busy = false;
  }
}

function openCreate() {
  createForm.username = "";
  createForm.password = "";
  createForm.role = "operator";
  createForm.permissions = {
    operations_view: false,
    operations_task_manage: false,
    operations_export: false,
    operations_rule_manage: false,
    operations_open_backend: false,
  };
  modal.open = true;
  modal.title = "新建子账号";
  modal.subtitle = "账号由现有云端团队服务创建，运营权限随后立即生效";
  modal.busy = false;
  modal.action = async () => {
    await call("team.operations.create", {
      username: createForm.username,
      password: createForm.password,
      role: createForm.role,
      permissions: operationPermissionPayload(null, createForm.permissions),
    });
    modal.open = false;
    await load();
    showFeedback("子账号已创建，运营中心权限已同步");
  };
}

function auditText(item) {
  try {
    const detail = JSON.parse(item.detail || "{}");
    const changes = Object.entries(detail.new || {})
      .map(([key, value]) => {
        const label = operationTags.find(([permission]) => permission === key)?.[1] || key;
        return `${label}:${value ? "开启" : "关闭"}`;
      })
      .join("，");
    return `${detail.actor || "主账号"} → ${detail.target_username || "成员"}：${changes || item.message}`;
  } catch {
    return item.message || item.operation;
  }
}

onMounted(load);
</script>

<template>
  <section class="perm-page">
    <header class="perm-header">
      <div>
        <h1>团队与功能权限</h1>
        <p>运营中心默认只对主账号开放，普通成员必须明确授权。保存后下一次请求立即生效。</p>
      </div>
      <div class="perm-actions">
        <label class="perm-search"><Icon name="search" :size="15" /><input v-model="search" type="search" placeholder="搜索用户名或成员 ID" /></label>
        <select v-model="roleFilter" class="perm-select"><option value="">全部角色</option><option value="owner">主账号</option><option value="admin">共同主账号</option><option value="manager">管理员</option><option value="operator">运营员</option><option value="viewer">只读</option></select>
        <button class="perm-secondary" type="button" @click="openBatch(true)">批量授权</button>
        <button class="perm-secondary" type="button" @click="openExistingAccount">添加已有账号</button>
        <button class="perm-primary" type="button" @click="openCreate">新建子账号</button>
      </div>
    </header>

    <div class="perm-notice"><Icon name="alert" :size="17" /><span>菜单隐藏、前端路由守卫和后端 RPC 403 三层同时生效。云端权限无法确认时按拒绝处理。</span></div>
    <div v-if="feedback.message" class="perm-toast" :class="feedback.tone">{{ feedback.message }}</div>
    <div v-if="loading" class="perm-state">正在读取真实团队成员和权限…</div>
    <div v-else-if="error" class="perm-state error">{{ error }}</div>
    <template v-else>
      <div class="perm-stats">
        <article><span>团队账号</span><strong>{{ stats.total }}</strong><small>真实成员列表</small></article>
        <article><span>可访问运营中心</span><strong>{{ stats.access }}</strong><small>当前云端权限</small></article>
        <article><span>可打开官方后台</span><strong>{{ stats.backend }}</strong><small>敏感能力</small></article>
        <article><span>已选择</span><strong>{{ selected.size }}</strong><small>待批量操作</small></article>
      </div>
      <section class="perm-card">
        <div class="perm-card-head">
          <div><h2>运营中心授权</h2><p>主账号固定拥有全部权限；其余角色默认无运营中心权限。</p></div>
          <div class="perm-segment"><button v-for="item in [['all','全部'],['enabled','已开启'],['disabled','未开启']]" :key="item[0]" type="button" :class="{ active: accessFilter === item[0] }" @click="accessFilter = item[0]">{{ item[1] }}</button></div>
        </div>
        <div class="perm-batchbar">
          <label><input type="checkbox" @change="toggleAll" />全选当前结果</label>
          <button type="button" @click="openBatch(true)">批量开启</button>
          <button type="button" @click="openBatch(false)">批量关闭</button>
          <span>撤权后已打开页面在下一次请求时返回 403，并清除运营摘要。</span>
        </div>
        <div class="perm-table-wrap">
          <table class="perm-table">
            <thead><tr><th>选择</th><th>成员</th><th>角色</th><th>店铺范围</th><th>运营中心</th><th>细分权限</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="member in filteredMembers" :key="member.member_id">
                <td><input type="checkbox" :disabled="member.role === 'owner'" :checked="selected.has(member.member_id)" @change="toggleOne(member)" /></td>
                <td class="perm-member">
                  <strong>{{ displayName(member) }}</strong>
                  <small>{{ member.member_id }} · {{ member.is_active === false ? "已停用" : "有效" }}</small>
                  <small v-if="member.operations_changed_by">最近：{{ member.operations_changed_at }} · {{ member.operations_changed_by }}</small>
                </td>
                <td><span class="perm-role">{{ roleLabel(member.role) }}</span></td>
                <td>{{ member.store_count ?? "全部已授权店铺" }}</td>
                <td><span class="perm-switch" :class="{ on: member.permissions?.operations_view, disabled: member.role === 'owner' }"></span></td>
                <td><span v-for="[key, label] in operationTags" :key="key" class="perm-tag" :class="{ allowed: member.permissions?.[key] }">{{ label }}</span></td>
                <td><button class="perm-edit" type="button" @click="openDrawer(member)">{{ member.role === 'owner' ? '查看' : '编辑' }}</button></td>
              </tr>
              <tr v-if="!filteredMembers.length"><td colspan="7" class="perm-empty">没有符合筛选条件的成员。</td></tr>
            </tbody>
          </table>
        </div>
      </section>
      <section class="perm-card">
        <div class="perm-card-head"><div><h2>最近权限变更</h2><p>记录操作人、目标账号、旧值、新值、IP 和时间。</p></div><span class="perm-muted">最近 {{ audit.length }} 条</span></div>
        <div v-if="!audit.length" class="perm-empty">暂无运营权限审计记录。</div>
        <div v-for="item in audit" :key="item.id" class="perm-audit-row"><span>{{ item.finished_at || item.started_at }}</span><strong>{{ auditText(item) }}</strong><small>{{ item.message }}</small></div>
      </section>
    </template>

    <div v-if="drawer.open" class="perm-mask" @click.self="closeDrawer"></div>
    <aside v-if="drawer.open" class="perm-drawer">
      <header><div><h2>{{ displayName(drawer.member) }}</h2><p>{{ roleLabel(drawer.member?.role) }} · 保存后下一次请求立即生效</p></div><button type="button" @click="closeDrawer"><Icon name="close" :size="18" /></button></header>
      <div class="perm-drawer-body">
        <div class="perm-notice"><Icon name="alert" :size="16" /><span>权限同时控制菜单可见性、路由访问和后端 RPC。隐藏菜单不能代替服务端鉴权。</span></div>
        <label v-for="[key, label, detail] in permissionOptions" :key="key" class="perm-permission">
          <div><strong>{{ label }}</strong><span>{{ detail }}</span></div>
          <input v-model="drawer.permissions[key]" type="checkbox" :disabled="drawer.member?.role === 'owner'" />
        </label>
      </div>
      <footer><button class="perm-secondary" type="button" @click="closeDrawer">取消</button><button class="perm-primary" type="button" :disabled="drawer.busy || drawer.member?.role === 'owner'" @click="saveDrawer">{{ drawer.busy ? "保存中…" : "保存并生效" }}</button></footer>
    </aside>

    <ModalDialog :open="modal.open" :title="modal.title" :subtitle="modal.subtitle" :confirm-label="modal.busy ? '保存中…' : '确认授权'" :busy="modal.busy" width="620px" z-index="145" @close="modal.open = false" @confirm="confirmModal">
      <div v-if="modal.title === '新建子账号'" class="perm-form">
        <label>用户名<input v-model="createForm.username" class="perm-input" autocomplete="off" /></label>
        <label>初始密码<input v-model="createForm.password" class="perm-input" type="password" autocomplete="new-password" /></label>
        <label>角色<select v-model="createForm.role" class="perm-select"><option value="manager">管理员</option><option value="operator">运营员</option><option value="viewer">只读</option></select></label>
        <div class="perm-form-permissions"><strong>运营中心权限</strong><label v-for="[key, label] in operationTags" :key="key"><input v-model="createForm.permissions[key]" type="checkbox" />{{ label }}</label></div>
      </div>
      <div v-else>
        <p>将变更 {{ selectedMembers.length }} 名成员的运营中心总开关。主账号不会被修改。</p>
        <p class="perm-muted">该操作会写入云端权限和本地审计日志。</p>
      </div>
    </ModalDialog>

    <ModalDialog :open="existingOpen" title="添加已有账号" subtitle="将已存在的澜琅账号加入当前团队" :confirm-label="existingBusy ? '添加中…' : '确认添加'" :busy="existingBusy" width="520px" z-index="145" @close="existingOpen = false" @confirm="addExistingAccount">
      <div class="perm-dialog-form">
        <label>账号用户名<input v-model="existingForm.username" class="perm-input" autocomplete="off" /></label>
        <label>团队角色<select v-model="existingForm.role" class="perm-select"><option value="admin">共同主账号</option><option value="manager">管理员</option><option value="operator">运营员</option><option value="viewer">只读</option></select></label>
      </div>
      <p class="perm-dialog-note">只添加已有账号，不创建密码、不读取其他账号凭据。运营中心细项权限仍由主账号另行授权。</p>
    </ModalDialog>
  </section>
</template>

<style scoped>
.perm-page { --line: #e1e8ef; --green: #0da66b; --ink: #263543; --muted: #7d8a96; min-height: 100%; padding: 22px 25px 30px; background: #f3f6f9; color: var(--ink); }
.perm-page button, .perm-page input, .perm-page select { font: inherit; }
.perm-header { display: flex; justify-content: space-between; gap: 20px; align-items: flex-start; }
.perm-header h1 { margin: 0; font-size: 23px; }
.perm-header p { margin: 6px 0 0; color: var(--muted); font-size: 12px; }
.perm-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.perm-search { min-width: 260px; height: 34px; display: flex; align-items: center; gap: 7px; padding: 0 10px; border: 1px solid var(--line); border-radius: 7px; background: white; }
.perm-search input { width: 100%; border: 0; outline: 0; background: transparent; }
.perm-select, .perm-input { min-height: 34px; padding: 7px 9px; border: 1px solid var(--line); border-radius: 7px; background: white; }
.perm-primary, .perm-secondary { min-height: 34px; padding: 0 12px; border-radius: 7px; border: 1px solid transparent; cursor: pointer; }
.perm-primary { background: var(--green); color: white; }
.perm-secondary { background: white; border-color: var(--line); color: #425362; }
.perm-primary:hover, .perm-secondary:hover { transform: translateY(-1px); box-shadow: 0 4px 10px rgba(30, 55, 68, .09); }
.perm-primary:disabled, .perm-secondary:disabled { opacity: .48; cursor: not-allowed; }
.perm-notice { display: flex; align-items: flex-start; gap: 8px; margin: 14px 0 12px; padding: 10px 12px; border: 1px solid #cfe8dc; border-radius: 8px; background: #edf9f3; color: #32725b; font-size: 11px; line-height: 1.6; }
.perm-toast { position: sticky; top: 7px; z-index: 20; width: fit-content; margin: 0 0 10px auto; padding: 8px 12px; border: 1px solid #b8e4cf; border-radius: 7px; background: #effbf4; color: #08734b; }
.perm-toast.error { border-color: #f0c3c8; background: #fff1f2; color: #a9333e; }
.perm-state { padding: 35px; border: 1px solid var(--line); border-radius: 10px; background: white; text-align: center; }
.perm-state.error { color: #aa3742; }
.perm-stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-bottom: 12px; }
.perm-stats article { padding: 13px 15px; border: 1px solid var(--line); border-radius: 9px; background: white; }
.perm-stats span, .perm-stats small { display: block; color: var(--muted); font-size: 10px; }
.perm-stats strong { display: block; margin: 7px 0 4px; font-size: 24px; }
.perm-card { margin-bottom: 12px; padding: 15px; border: 1px solid var(--line); border-radius: 10px; background: white; }
.perm-card-head { display: flex; justify-content: space-between; gap: 15px; align-items: flex-start; }
.perm-card-head h2 { margin: 0; font-size: 15px; }
.perm-card-head p { margin: 4px 0 0; color: var(--muted); font-size: 10px; }
.perm-segment { display: flex; gap: 2px; padding: 2px; border-radius: 6px; background: #f1f5f7; }
.perm-segment button { border: 0; border-radius: 5px; padding: 5px 9px; background: transparent; color: #697886; font-size: 10px; cursor: pointer; }
.perm-segment button.active { background: white; color: #16764e; box-shadow: 0 1px 3px rgba(31,56,70,.1); }
.perm-batchbar { display: flex; align-items: center; gap: 8px; margin: 12px 0 9px; padding: 8px; border-radius: 7px; background: #f7f9fb; color: #687785; font-size: 10px; }
.perm-batchbar button { border: 0; background: transparent; color: #16764e; cursor: pointer; }
.perm-batchbar span { margin-left: auto; color: #929ca4; }
.perm-table-wrap { overflow: auto; border: 1px solid #edf1f4; border-radius: 8px; }
.perm-table { width: 100%; min-width: 920px; border-collapse: collapse; font-size: 11px; }
.perm-table th { position: sticky; top: 0; padding: 8px 9px; background: #f7f9fb; color: #72808d; text-align: left; font-weight: 500; }
.perm-table td { padding: 9px; border-top: 1px solid #eff3f5; vertical-align: middle; }
.perm-member strong, .perm-member small { display: block; }
.perm-member small { margin-top: 3px; color: #8d98a1; font-size: 9px; }
.perm-role { padding: 2px 7px; border-radius: 99px; background: #eef2f6; color: #526474; font-size: 9px; }
.perm-switch { display: inline-block; width: 30px; height: 17px; border-radius: 99px; background: #cbd3db; position: relative; }
.perm-switch::after { content: ""; position: absolute; top: 2px; left: 2px; width: 13px; height: 13px; border-radius: 50%; background: white; box-shadow: 0 1px 2px rgba(0,0,0,.16); }
.perm-switch.on { background: var(--green); }
.perm-switch.on::after { left: 15px; }
.perm-switch.disabled { opacity: .7; }
.perm-tag { display: inline-block; margin: 1px 3px 1px 0; padding: 2px 5px; border-radius: 4px; background: #f0f3f6; color: #9aa4ad; font-size: 9px; }
.perm-tag.allowed { background: #e7f7ef; color: #08774e; }
.perm-edit { border: 0; background: transparent; color: #16764e; cursor: pointer; }
.perm-empty { padding: 28px; text-align: center; color: #8a969f; }
.perm-audit-row { display: grid; grid-template-columns: 145px 1fr 130px; gap: 10px; padding: 9px 3px; border-top: 1px solid #eff3f5; font-size: 10px; }
.perm-audit-row span, .perm-audit-row small { color: var(--muted); }
.perm-muted { color: var(--muted); font-size: 10px; }
.perm-mask { position: fixed; inset: 0; z-index: 130; background: rgba(15,30,41,.3); }
.perm-drawer { position: fixed; top: 0; right: 0; bottom: 0; z-index: 131; width: min(470px, 94vw); display: flex; flex-direction: column; background: #f7f9fb; box-shadow: -14px 0 35px rgba(14,31,43,.18); }
.perm-drawer header { display: flex; justify-content: space-between; padding: 17px; background: #183c52; color: white; }
.perm-drawer header h2 { margin: 0; font-size: 17px; }
.perm-drawer header p { margin: 5px 0 0; color: #c3d0d9; font-size: 10px; }
.perm-drawer header button { width: 31px; height: 31px; border: 0; border-radius: 6px; background: rgba(255,255,255,.12); color: white; }
.perm-drawer-body { flex: 1; overflow: auto; padding: 14px; }
.perm-permission { display: flex; justify-content: space-between; gap: 15px; padding: 12px 3px; border-bottom: 1px solid #e9eef2; }
.perm-permission strong, .perm-permission span { display: block; }
.perm-permission strong { font-size: 11px; }
.perm-permission span { margin-top: 4px; color: var(--muted); font-size: 9px; }
.perm-drawer footer { display: flex; justify-content: flex-end; gap: 8px; padding: 13px; border-top: 1px solid #e5ebef; background: white; }
.perm-form { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.perm-form label { display: grid; gap: 5px; color: #5f6f7c; font-size: 10px; }
.perm-form-permissions { grid-column: 1 / -1; display: flex; gap: 12px; flex-wrap: wrap; padding: 10px; border: 1px solid var(--line); border-radius: 7px; }
.perm-form-permissions strong { width: 100%; font-size: 11px; }
.perm-form-permissions label { display: flex; grid-template-columns: auto 1fr; align-items: center; gap: 5px; }
.perm-dialog-form { display: grid; gap: 12px; }
.perm-dialog-form label { display: grid; gap: 6px; color: #5f6f7c; font-size: 11px; }
.perm-dialog-note { margin: 12px 0 0; color: #7c8994; font-size: 10px; line-height: 1.7; }
@media (max-width: 900px) {
  .perm-header { flex-direction: column; }
  .perm-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .perm-audit-row { grid-template-columns: 1fr; }
}
@media (max-width: 600px) {
  .perm-page { padding: 12px; }
  .perm-stats, .perm-form { grid-template-columns: 1fr; }
  .perm-actions { width: 100%; }
  .perm-search { width: 100%; min-width: 0; }
}
</style>

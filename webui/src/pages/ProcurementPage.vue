<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { call } from "../api";
import Icon from "../components/Icon.vue";
import ModalDialog from "../components/ModalDialog.vue";

const accounts = ref([]);
const loading = ref(false);
const busyAccountId = ref(null);
const busyAction = ref("");
const note = ref("");
const outcome = reactive({
  tone: "",
  title: "",
  message: "",
});
const dialogOpen = ref(false);
const deleteConfirmOpen = ref(false);
const pendingDeleteAccount = ref(null);
const deleting = ref(false);
const platformFilter = ref("");
const form = reactive({
  id: null,
  platform: "淘宝",
  label: "",
  nickname: "",
  group_name: "",
  remark: "",
  login_id: "",
  password: "",
  alipay_password: "",
  auto_login: false,
});
const platformTabs = [
  { value: "", label: "全部账号" },
  { value: "淘宝", label: "淘宝" },
  { value: "1688", label: "1688" },
];
const filteredAccounts = computed(() => (
  platformFilter.value
    ? accounts.value.filter((account) => account.platform === platformFilter.value)
    : accounts.value
));

async function load() {
  loading.value = true;
  try {
    accounts.value = (await call("procurement.list", {})).accounts || [];
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loading.value = false;
  }
}

async function runAccountAction(account, action) {
  if (busyAccountId.value) return;
  busyAccountId.value = account.id;
  busyAction.value = action;
  note.value = action === "login"
    ? "正在打开采购账号登录…"
    : action === "backend"
      ? "正在打开采购账号官方后台…"
      : "正在同步采购数据…";
  if (action === "login") {
    Object.assign(outcome, {
      tone: "waiting",
      title: "等待官方登录",
      message: "请在扫码窗口完成登录；官方登录完成后窗口会自动关闭。",
    });
  } else if (action === "backend") {
    Object.assign(outcome, {
      tone: "waiting",
      title: "官方后台已打开",
      message: "正在核验该账号的持久化会话和官方身份。",
    });
  }
  try {
    if (action === "login") {
      const started = await call("procurement.login.start", {
        account_id: account.id,
        scan_only: true,
      });
      let job = null;
      while (true) {
        job = await call("procurement.login.status", {
          job_id: started.job_id,
        });
        note.value = job.message || "等待采购账号扫码登录…";
        if (job.status === "completed") break;
        if (job.status === "failed") {
          throw new Error(job.error || job.message || "采购账号登录失败");
        }
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
      note.value = (
        "采购账号登录成功"
        + (job.identity ? `：${job.identity}` : "")
        + "；登录会话已安全保存"
      );
      Object.assign(outcome, {
        tone: "success",
        title: "登录成功",
        message: `采购账号“${account.label}”已通过官方会话校验，登录窗口已自动关闭。`,
      });
    } else if (action === "backend") {
      const started = await call("procurement.open-backend.start", {
        account_id: account.id,
      });
      let job = null;
      while (true) {
        job = await call("procurement.open-backend.status", {
          job_id: started.job_id,
        });
        note.value = job.message || "采购账号官方后台打开中…";
        if (job.status === "completed") break;
        if (job.status === "failed") {
          throw new Error(job.error || job.message || "打开官方后台失败");
        }
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
      note.value = `采购账号官方后台已核验：${job.identity || account.label}`;
      Object.assign(outcome, {
        tone: "success",
        title: "官方后台已打开",
        message: `已使用“${account.label}”自己的持久化 Profile 打开官方页面；关闭浏览器后操作结束。`,
      });
    } else {
      const result = await call("procurement.sync", {
        account_id: account.id,
      });
      note.value = result.result?.message || "采购数据同步完成";
    }
    await load();
  } catch (cause) {
    const message = cause instanceof Error ? cause.message : String(cause);
    note.value = message;
    if (action === "login") {
      Object.assign(outcome, {
        tone: "danger",
        title: "登录未完成",
        message,
      });
    }
  } finally {
    busyAccountId.value = null;
    busyAction.value = "";
  }
}

async function probeSavedSessions() {
  // Probe once per software run, not on every route mount/refresh.  Repeated
  // headless visits are a major trigger for 1688's temporary challenges.
  const candidates = accounts.value.filter((account) => (
    account.has_session
    && !sessionStorage.getItem(`procurement-probed:${account.id}`)
  ));
  for (const account of candidates) {
    sessionStorage.setItem(`procurement-probed:${account.id}`, "1");
    try {
      const result = await call("procurement.probe", {
        account_id: account.id,
      });
      const row = accounts.value.find((item) => item.id === account.id);
      if (row) {
        Object.assign(row, result.account || {});
        row.status = result.status;
        row.has_session = true;
      }
    } catch (cause) {
      sessionStorage.removeItem(`procurement-probed:${account.id}`);
      note.value = (
        `${account.label} 官方会话校验失败：`
        + (cause instanceof Error ? cause.message : String(cause))
      );
    }
  }
}

function openCreate() {
  Object.assign(form, {
    id: null,
    platform: "淘宝",
    label: "",
    nickname: "",
    group_name: "",
    remark: "",
    login_id: "",
    password: "",
    alipay_password: "",
    auto_login: false,
  });
  dialogOpen.value = true;
}

function openEdit(account) {
  Object.assign(form, account, {
    password: "",
    alipay_password: "",
  });
  dialogOpen.value = true;
}

async function save() {
  note.value = "";
  try {
    if (form.id) {
      await call("procurement.update", {
        account_id: form.id,
        label: form.label,
        nickname: form.nickname,
        group_name: form.group_name,
        remark: form.remark,
        login_id: form.login_id,
        password: form.password || undefined,
        alipay_password: form.alipay_password || undefined,
        auto_login: form.auto_login,
      });
    } else {
      await call("procurement.create", { ...form });
    }
    dialogOpen.value = false;
    await load();
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  }
}

async function remove(account) {
  pendingDeleteAccount.value = account;
  deleteConfirmOpen.value = true;
}

async function confirmRemove() {
  const account = pendingDeleteAccount.value;
  if (!account || deleting.value) return;
  deleting.value = true;
  try {
    await call("procurement.delete", { account_id: account.id });
    deleteConfirmOpen.value = false;
    pendingDeleteAccount.value = null;
    await load();
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    deleting.value = false;
  }
}

function closeDeleteConfirm() {
  if (deleting.value) return;
  deleteConfirmOpen.value = false;
  pendingDeleteAccount.value = null;
}

onMounted(async () => {
  await load();
  await probeSavedSessions();
});
</script>

<template>
  <section class="page">
    <header class="page-header">
      <div><h1>采购账号</h1></div>
      <div class="toolbar">
        <button class="btn" type="button" @click="load"><Icon name="refresh" :size="15" /> 刷新</button>
        <button class="btn primary" type="button" @click="openCreate"><Icon name="plus" :size="15" /> 添加账号</button>
      </div>
    </header>

    <div class="procurement-tabs">
      <button v-for="tab in platformTabs" :key="tab.value" class="chip" :class="{ active: platformFilter === tab.value }" type="button" @click="platformFilter = tab.value">
        {{ tab.label }}
      </button>
    </div>

    <div v-if="outcome.title" class="login-outcome" :class="outcome.tone" role="status" aria-live="polite">
      <strong>{{ outcome.title }}</strong>
      <span>{{ outcome.message }}</span>
    </div>

    <div class="card procurement-card">
      <table class="data-table">
        <thead>
          <tr>
            <th>序号</th>
            <th>平台</th>
            <th>账号名称</th>
            <th>昵称</th>
            <th>分组</th>
            <th>登录状态</th>
            <th>备注</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(account, index) in filteredAccounts" :key="account.id">
            <td>{{ index + 1 }}</td>
            <td>{{ account.platform }}</td>
            <td>{{ account.label }}</td>
            <td>{{ account.nickname }}</td>
            <td>{{ account.group_name || "-" }}</td>
            <td><span class="account-status" :class="{ danger: /失效|掉线|不匹配/.test(account.status), success: account.status === '已登录' }">{{ account.status }}</span></td>
            <td>{{ account.remark || "-" }}</td>
            <td>
              <div class="action-group">
                <button
                  class="pill-button"
                  type="button"
                  :disabled="Boolean(busyAccountId)"
                  @click="runAccountAction(account, 'login')"
                >{{ busyAccountId === account.id && busyAction === 'login' ? "等待登录..." : "扫码登录" }}</button>
                <button
                  class="pill-button"
                  type="button"
                  :disabled="Boolean(busyAccountId) || account.status !== '已登录'"
                  :title="account.status === '已登录' ? '使用该账号自己的登录会话打开官方个人中心/工作台' : '请先扫码登录'"
                  @click="runAccountAction(account, 'backend')"
                >{{ busyAccountId === account.id && busyAction === 'backend' ? "后台已打开..." : "打开后台" }}</button>
                <button
                  class="pill-button gray"
                  type="button"
                  :disabled="Boolean(busyAccountId)"
                  @click="runAccountAction(account, 'sync')"
                >{{ busyAccountId === account.id && busyAction === 'sync' ? "同步中..." : "同步" }}</button>
                <button class="pill-button gray" type="button" @click="openEdit(account)">修改</button>
                <button class="pill-button red" type="button" @click="remove(account)">删除</button>
              </div>
            </td>
          </tr>
          <tr v-if="!loading && filteredAccounts.length === 0">
            <td colspan="8" class="empty-state">当前筛选下暂无采购账号</td>
          </tr>
        </tbody>
      </table>
    </div>

    <ModalDialog :open="dialogOpen" :title="form.id ? '修改采购账号' : '添加采购账号'" width="620px" @close="dialogOpen = false">
      <div class="form-grid">
        <div class="field"><label>平台</label><select v-model="form.platform" class="select"><option>淘宝</option><option>1688</option></select></div>
        <div class="field"><label>账号名称</label><input v-model="form.label" class="input" /></div>
        <div class="field"><label>昵称</label><input v-model="form.nickname" class="input" /></div>
        <div class="field"><label>分组</label><input v-model="form.group_name" class="input" /></div>
        <div class="field"><label>登录账号</label><input v-model="form.login_id" class="input" /></div>
        <div class="field"><label>登录密码</label><input v-model="form.password" class="input" type="password" placeholder="留空表示不修改" /></div>
        <div class="field"><label>支付宝密码</label><input v-model="form.alipay_password" class="input" type="password" placeholder="留空表示不修改" /></div>
        <div class="field full"><label>备注</label><input v-model="form.remark" class="input" /></div>
        <div class="field full"><label><input v-model="form.auto_login" type="checkbox" /> 自动登录</label></div>
      </div>
      <template #footer>
        <button class="btn" type="button" @click="dialogOpen = false">取消</button>
        <button class="btn primary" type="button" @click="save">保存</button>
      </template>
    </ModalDialog>
    <ModalDialog :open="deleteConfirmOpen" title="删除采购账号" :subtitle="pendingDeleteAccount?.label || ''" width="500px" @close="closeDeleteConfirm">
      <p class="delete-account-warning">删除后本机保存的采购登录状态和相关密码将被移除。该操作会影响订单工作台中的采购账号选择，是否继续？</p>
      <template #footer>
        <button class="btn" type="button" :disabled="deleting" @click="closeDeleteConfirm">取消</button>
        <button class="btn danger" type="button" :disabled="deleting" @click="confirmRemove">{{ deleting ? "删除中..." : "确认删除" }}</button>
      </template>
    </ModalDialog>
    <div v-if="note" class="toast">{{ note }}</div>
  </section>
</template>

<style scoped>
.procurement-tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 14px;
}

.procurement-card {
  min-height: 480px;
}

.login-outcome {
  margin-bottom: 12px;
  padding: 10px 13px;
  display: grid;
  gap: 3px;
  border: 1px solid #d8e2ea;
  border-radius: 7px;
  background: #f7f9fb;
  color: #52677a;
  font-size: 11px;
}

.login-outcome strong {
  font-size: 12px;
}

.login-outcome.waiting {
  border-color: #bad4f3;
  background: #f2f8ff;
  color: #2c629e;
}

.login-outcome.success {
  border-color: #bce0cd;
  background: #f1faf5;
  color: #257049;
}

.login-outcome.danger {
  border-color: #ebc3c8;
  background: #fff5f6;
  color: #a43843;
}

.account-status {
  padding: 3px 7px;
  border-radius: 99px;
  color: #67798a;
  background: #edf2f5;
  font-size: 10px;
  white-space: nowrap;
}

.account-status.success {
  color: #237249;
  background: #e9f8ef;
}

.account-status.danger {
  color: #ad3541;
  background: #fff0f1;
}

.delete-account-warning {
  margin: 0;
  color: #68414a;
  font-size: 11px;
  line-height: 1.7;
}
</style>

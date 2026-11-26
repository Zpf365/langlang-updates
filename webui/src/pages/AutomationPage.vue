<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { call } from "../api";
import Icon from "../components/Icon.vue";
import ModalDialog from "../components/ModalDialog.vue";

const stores = ref([]);
const tasks = ref([]);
const runs = ref([]);
const taskTypes = ref([]);
const selectedIds = ref([]);
const loading = ref(false);
const note = ref("");
const editorOpen = ref(false);
const deleteOpen = ref(false);
const pendingDelete = ref(null);
const job = reactive({
  id: "",
  status: "",
  message: "",
  current: 0,
  total: 0,
  succeeded: 0,
  failed: 0,
  errors: [],
});
const form = reactive({
  id: null,
  name: "",
  task_type: "order_sync",
  store_id: 0,
  interval_minutes: 60,
  enabled: true,
});

const running = computed(() => ["running", "starting"].includes(job.status));
const progressPercent = computed(() => (
  job.total ? Math.round(job.current / job.total * 100) : 0
));

function taskTypeLabel(value) {
  return taskTypes.value.find((row) => row.value === value)?.label || value || "未知任务";
}

function toggleSelected(taskId) {
  selectedIds.value = selectedIds.value.includes(taskId)
    ? selectedIds.value.filter((value) => value !== taskId)
    : [...selectedIds.value, taskId];
}

function toggleAll() {
  selectedIds.value = selectedIds.value.length === tasks.value.length
    ? []
    : tasks.value.map((task) => Number(task.id));
}

async function load() {
  loading.value = true;
  try {
    const [taskResult, storeResult] = await Promise.all([
      call("automation.tasks.list", {}),
      call("stores.list", {}),
    ]);
    tasks.value = taskResult.tasks || [];
    runs.value = taskResult.runs || [];
    taskTypes.value = taskResult.task_types || [];
    stores.value = storeResult.stores || [];
    selectedIds.value = selectedIds.value.filter((id) => tasks.value.some((task) => Number(task.id) === id));
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loading.value = false;
  }
}

function openCreate() {
  Object.assign(form, {
    id: null,
    name: "",
    task_type: "order_sync",
    store_id: 0,
    interval_minutes: 60,
    enabled: true,
  });
  editorOpen.value = true;
}

function openEdit(task) {
  Object.assign(form, {
    id: Number(task.id),
    name: task.name || "",
    task_type: task.task_type || "order_sync",
    store_id: Number(task.store_id || 0),
    interval_minutes: Number(task.interval_minutes || 60),
    enabled: Boolean(task.enabled),
  });
  editorOpen.value = true;
}

async function save() {
  loading.value = true;
  note.value = "";
  try {
    if (form.id) {
      await call("automation.tasks.update", {
        task_id: form.id,
        name: form.name,
        task_type: form.task_type,
        store_id: form.store_id || null,
        interval_minutes: form.interval_minutes,
        enabled: form.enabled,
      });
    } else {
      await call("automation.tasks.create", {
        name: form.name,
        task_type: form.task_type,
        store_id: form.store_id || null,
        interval_minutes: form.interval_minutes,
        enabled: form.enabled,
      });
    }
    editorOpen.value = false;
    await load();
    note.value = form.id ? "自动化任务已更新" : "自动化任务已创建";
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loading.value = false;
  }
}

async function toggleTask(task) {
  loading.value = true;
  try {
    await call("automation.tasks.update", {
      task_id: Number(task.id),
      enabled: !task.enabled,
    });
    await load();
    note.value = task.enabled ? "任务已停用" : "任务已启用";
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loading.value = false;
  }
}

function requestDelete(task) {
  pendingDelete.value = task;
  deleteOpen.value = true;
}

async function confirmDelete() {
  if (!pendingDelete.value || loading.value) return;
  loading.value = true;
  try {
    await call("automation.tasks.delete", {
      task_id: Number(pendingDelete.value.id),
    });
    deleteOpen.value = false;
    pendingDelete.value = null;
    await load();
    note.value = "自动化任务已删除，历史执行记录保留";
  } catch (cause) {
    note.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loading.value = false;
  }
}

async function runTasks(ids) {
  const taskIds = [...new Set(ids)].filter(Boolean);
  if (!taskIds.length || running.value) {
    if (!taskIds.length) note.value = "请先选择自动化任务";
    return;
  }
  loading.value = true;
  note.value = "正在启动自动化任务…";
  try {
    const started = await call("automation.tasks.run-start", { task_ids: taskIds });
    Object.assign(job, {
      id: started.job_id,
      status: started.status || "running",
      message: "自动化任务已启动",
      current: 0,
      total: started.total || taskIds.length,
      succeeded: 0,
      failed: 0,
      errors: [],
    });
    while (running.value) {
      await new Promise((resolve) => window.setTimeout(resolve, 700));
      const status = await call("automation.tasks.run-status", { job_id: job.id });
      Object.assign(job, status);
      note.value = job.message || "自动化任务执行中…";
    }
    await load();
    note.value = job.errors?.length
      ? `${job.message}；${job.errors.slice(0, 3).join("；")}`
      : job.message;
  } catch (cause) {
    job.status = "failed";
    note.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <section class="page automation-page">
    <header class="page-header">
      <div>
        <h1>自动化</h1>
        <p>复用旧版真实同步服务，按店铺执行订单、商品、售后、经营数据、采购和托管任务。</p>
      </div>
      <div class="toolbar">
        <button class="btn" type="button" :disabled="loading || running" @click="load">
          <Icon name="refresh" :size="15" /> 刷新
        </button>
        <button class="btn primary" type="button" :disabled="loading || running" @click="openCreate">
          <Icon name="plus" :size="15" /> 新增任务
        </button>
        <button class="btn blue" type="button" :disabled="!selectedIds.length || loading || running" @click="runTasks(selectedIds)">
          {{ running ? "执行中…" : `立即执行 ${selectedIds.length || ""}` }}
        </button>
      </div>
    </header>

    <div v-if="job.status" class="automation-progress card" :class="job.status">
      <strong>{{ job.message }}</strong>
      <span>{{ job.current }} / {{ job.total }}</span>
      <div class="progress-track"><i :style="{ width: `${progressPercent}%` }" /></div>
      <small>成功 {{ job.succeeded }} · 失败 {{ job.failed }}</small>
    </div>

    <div class="card table-card">
      <div class="table-scroll">
        <table class="data-table">
          <thead>
            <tr>
              <th><input type="checkbox" :checked="tasks.length > 0 && selectedIds.length === tasks.length" @change="toggleAll" /></th>
              <th>任务名称</th>
              <th>执行动作</th>
              <th>目标店铺</th>
              <th>间隔</th>
              <th>状态</th>
              <th>最近执行</th>
              <th>下次执行</th>
              <th>结果</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="task in tasks" :key="task.id">
              <td><input type="checkbox" :checked="selectedIds.includes(Number(task.id))" @change="toggleSelected(Number(task.id))" /></td>
              <td><strong>{{ task.name }}</strong></td>
              <td>{{ taskTypeLabel(task.task_type) }}</td>
              <td>{{ task.store_name || "全部店铺" }}</td>
              <td>{{ task.interval_minutes }} 分钟</td>
              <td>{{ task.enabled ? "运行中" : "已暂停" }}</td>
              <td>{{ task.last_run || "暂无数据" }}</td>
              <td>{{ task.next_run || "暂无数据" }}</td>
              <td>
                <span :class="['task-result', task.last_status === '成功' ? 'success' : task.last_status === '失败' ? 'failed' : '']">{{ task.last_status || "暂无数据" }}</span>
                <small v-if="task.last_message">{{ task.last_message }}</small>
              </td>
              <td class="row-actions">
                <button type="button" :disabled="loading || running" @click="runTasks([Number(task.id)])">执行</button>
                <button type="button" :disabled="loading || running" @click="openEdit(task)">编辑</button>
                <button type="button" :disabled="loading || running" @click="toggleTask(task)">{{ task.enabled ? "停用" : "启用" }}</button>
                <button class="danger" type="button" :disabled="loading || running" @click="requestDelete(task)">删除</button>
              </td>
            </tr>
            <tr v-if="!loading && tasks.length === 0">
              <td colspan="10" class="empty-state">暂无自动化任务，请新增后执行真实同步</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <section class="card run-log">
      <header><strong>最近执行记录</strong><small>执行记录保存在本机 SQLite</small></header>
      <article v-for="row in runs.slice(0, 30)" :key="row.id">
        <span>{{ row.finished_at || row.started_at }}</span>
        <strong>{{ row.task_name }}</strong>
        <b :class="row.status">{{ row.status === "success" ? "成功" : "失败" }}</b>
        <p>{{ row.message || "暂无说明" }}</p>
      </article>
      <div v-if="!runs.length" class="empty-state">暂无自动化执行记录</div>
    </section>

    <ModalDialog :open="editorOpen" :title="form.id ? '编辑自动化任务' : '新增自动化任务'" width="620px" :busy="loading" @close="editorOpen = false">
      <div class="form-grid">
        <div class="field full"><label>任务名称</label><input v-model="form.name" class="input" maxlength="80" /></div>
        <div class="field">
          <label>执行动作</label>
          <select v-model="form.task_type" class="select">
            <option v-for="item in taskTypes" :key="item.value" :value="item.value">{{ item.label }}</option>
          </select>
        </div>
        <div class="field">
          <label>目标店铺</label>
          <select v-model.number="form.store_id" class="select">
            <option :value="0">全部店铺</option>
            <option v-for="store in stores" :key="store.id" :value="store.id">{{ store.name }}</option>
          </select>
        </div>
        <div class="field"><label>间隔分钟</label><input v-model.number="form.interval_minutes" class="input" type="number" min="1" max="10080" /></div>
        <div class="field"><label><input v-model="form.enabled" type="checkbox" /> 启用任务</label></div>
      </div>
      <template #footer>
        <button class="btn" type="button" :disabled="loading" @click="editorOpen = false">取消</button>
        <button class="btn primary" type="button" :disabled="loading || !form.name.trim()" @click="save">{{ loading ? "保存中…" : "保存" }}</button>
      </template>
    </ModalDialog>

    <ModalDialog :open="deleteOpen" title="删除自动化任务" :subtitle="pendingDelete?.name || ''" width="520px" :busy="loading" @close="deleteOpen = false">
      <p>删除后该任务不再执行，但已有历史执行记录会保留。是否继续？</p>
      <template #footer>
        <button class="btn" type="button" :disabled="loading" @click="deleteOpen = false">取消</button>
        <button class="btn danger" type="button" :disabled="loading" @click="confirmDelete">{{ loading ? "删除中…" : "确认删除" }}</button>
      </template>
    </ModalDialog>

    <div v-if="note" class="toast">{{ note }}</div>
  </section>
</template>

<style scoped>
.automation-page { display: grid; gap: 14px; }
.automation-progress { padding: 14px 18px; display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px 14px; align-items: center; }
.automation-progress span, .automation-progress small { color: #687785; }
.automation-progress .progress-track { grid-column: 1 / -1; height: 6px; overflow: hidden; border-radius: 99px; background: #e4edf2; }
.automation-progress .progress-track i { display: block; height: 100%; border-radius: inherit; background: #1db56b; transition: width .25s ease; }
.automation-progress.failed, .automation-progress.partial { border-color: #e6bdc2; background: #fff7f8; }
.table-card { overflow: auto; }
.data-table { min-width: 1320px; }
.row-actions { white-space: nowrap; }
.row-actions button { padding: 4px 7px; border: 0; color: #2e75a8; background: transparent; cursor: pointer; }
.row-actions button.danger { color: #cf4f59; }
.task-result { font-weight: 600; }
.task-result.success, .run-log b.success { color: #198254; }
.task-result.failed, .run-log b.failed { color: #c14550; }
.data-table td small { display: block; margin-top: 4px; max-width: 240px; color: #87939e; white-space: normal; }
.run-log { max-height: 320px; padding: 14px 18px; overflow: auto; }
.run-log header { display: flex; justify-content: space-between; margin-bottom: 10px; }
.run-log header small { color: #87939e; }
.run-log article { padding: 9px 0; display: grid; grid-template-columns: 150px 1fr 60px; gap: 8px; border-top: 1px solid #edf1f4; }
.run-log article span { color: #87939e; font-size: 11px; }
.run-log article p { grid-column: 1 / -1; margin: 0; color: #5b6874; }
</style>

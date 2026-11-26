<script setup>
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  watch,
} from "vue";
import { copyText } from "../clipboard";
import Icon from "./Icon.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  stores: { type: Array, default: () => [] },
  templates: { type: Array, default: () => [] },
  defaultTemplateId: { type: String, default: "" },
  initialStoreIds: { type: Array, default: () => [] },
  options: { type: Object, default: () => ({}) },
  productCount: { type: Number, default: 0 },
  busy: { type: Boolean, default: false },
});
const emit = defineEmits(["close", "load-options", "submit", "toast"]);

const selectedStoreIds = ref([]);
const selectedTemplateId = ref("");
const allocationMode = ref("average");
const configs = reactive({});
const hydratedStoreIds = new Set();
const touchedConfigKeys = new Set();
const menu = reactive({
  open: false,
  x: 0,
  y: 0,
  currentStoreId: 0,
  selectedIds: [],
});
const batchPanel = reactive({
  open: false,
  field: "",
  label: "",
});
let outsideHandler = null;
let keyHandler = null;

const groups = computed(() => {
  const counts = new Map();
  for (const store of props.stores) {
    const group = String(store.group_name || "未分组").trim() || "未分组";
    counts.set(group, Number(counts.get(group) || 0) + 1);
  }
  return [...counts.entries()].map(([name, count]) => ({ name, count }));
});
const selectedStores = computed(() => props.stores.filter(
  (store) => selectedStoreIds.value.includes(Number(store.id)),
));
const allSelected = computed(() => (
  props.stores.length > 0
  && props.stores.every((store) => selectedStoreIds.value.includes(Number(store.id)))
));
const selectedCountText = computed(() => (
  `已选 ${selectedStoreIds.value.length} / ${props.stores.length} 家`
));
const loadedCount = computed(() => selectedStores.value.filter(
  (store) => props.options?.[String(store.id)],
).length);
const canSubmit = computed(() => (
  selectedStoreIds.value.length > 0
  && Boolean(selectedTemplateId.value)
  && !props.busy
));
const batchOptions = computed(() => {
  if (!batchPanel.field) return [];
  const first = selectedStores.value[0];
  if (!first) return [];
  return fieldOptions(first.id, batchPanel.field);
});

function optionFor(storeId) {
  return props.options?.[String(storeId)] || {};
}

function uniqueOptions(rows, noBrand = false) {
  const seen = new Set();
  const output = [];
  for (const row of rows || []) {
    const id = String(row?.id ?? "").trim();
    if (!id || seen.has(id)) continue;
    seen.add(id);
    let label = String(row?.label || "").trim();
    if (noBrand && id === "2100000000") label = "无品牌";
    if (!label || label === id) {
      label = noBrand ? "未读取到品牌名称" : "未读取到名称";
    }
    output.push({ id, label });
  }
  return output;
}

function fieldOptions(storeId, field) {
  const source = optionFor(storeId);
  if (field === "freight_template_id") {
    return uniqueOptions(source.freight_templates);
  }
  if (field === "brand_id") {
    const rows = uniqueOptions(source.brands, true);
    return rows.length
      ? rows
      : [{ id: "2100000000", label: "无品牌" }];
  }
  return uniqueOptions(source.after_sale_addresses);
}

function defaultConfig(storeId) {
  const source = optionFor(storeId);
  const brands = fieldOptions(storeId, "brand_id");
  return {
    freight_template_id: String(source.default_freight_template_id || ""),
    brand_id: String(
      source.default_brand_id
      || brands.find((item) => item.id === "2100000000")?.id
      || brands[0]?.id
      || "2100000000",
    ),
    after_sale_address_id: String(source.default_after_sale_address_id || ""),
  };
}

function ensureConfigs() {
  for (const store of selectedStores.value) {
    const key = String(store.id);
    const defaults = defaultConfig(store.id);
    if (!configs[key]) {
      configs[key] = defaults;
    } else if (
      optionFor(store.id)
      && !hydratedStoreIds.has(key)
    ) {
      Object.assign(configs[key], defaults);
    }
    for (const field of Object.keys(defaults)) {
      if (configs[key][field] === undefined) {
        configs[key][field] = defaults[field];
      }
    }
    if (optionFor(store.id)) hydratedStoreIds.add(key);
  }
}

function configValue(storeId, field) {
  const key = `${storeId}:${field}`;
  if (touchedConfigKeys.has(key)) {
    return configs[String(storeId)]?.[field] ?? "";
  }
  return (
    configs[String(storeId)]?.[field]
    || defaultConfig(storeId)[field]
  );
}

function setConfig(storeId, field, value) {
  const key = String(storeId);
  if (!configs[key]) configs[key] = defaultConfig(storeId);
  configs[key][field] = value;
  touchedConfigKeys.add(`${storeId}:${field}`);
}

function initialize() {
  const known = new Set(props.stores.map((store) => Number(store.id)));
  let initial = props.initialStoreIds
    .map(Number)
    .filter((id) => known.has(id));
  if (!initial.length) {
    const preferred = props.stores.find((store) => (
      String(store.web_status || "").includes("已登录")
    )) || props.stores[0];
    initial = preferred ? [Number(preferred.id)] : [];
  }
  selectedStoreIds.value = [...new Set(initial)];
  selectedTemplateId.value = (
    props.defaultTemplateId
    || props.templates[0]?.id
    || ""
  );
  allocationMode.value = "average";
  batchPanel.open = false;
  menu.open = false;
  hydratedStoreIds.clear();
  touchedConfigKeys.clear();
  ensureConfigs();
  void nextTick(refreshOptions);
}

function close() {
  if (props.busy) return;
  menu.open = false;
  batchPanel.open = false;
  emit("close");
}

function toggleStore(storeId) {
  const id = Number(storeId);
  selectedStoreIds.value = selectedStoreIds.value.includes(id)
    ? selectedStoreIds.value.filter((value) => value !== id)
    : [...selectedStoreIds.value, id];
  ensureConfigs();
  void nextTick(refreshOptions);
}

function toggleGroup(group) {
  const members = props.stores
    .filter((store) => (String(store.group_name || "未分组").trim() || "未分组") === group.name)
    .map((store) => Number(store.id));
  const shouldSelect = !members.every((id) => selectedStoreIds.value.includes(id));
  selectedStoreIds.value = shouldSelect
    ? [...new Set([...selectedStoreIds.value, ...members])]
    : selectedStoreIds.value.filter((id) => !members.includes(id));
  ensureConfigs();
  void nextTick(refreshOptions);
}

function groupState(group) {
  const members = props.stores
    .filter((store) => (String(store.group_name || "未分组").trim() || "未分组") === group.name)
    .map((store) => Number(store.id));
  const selected = members.filter((id) => selectedStoreIds.value.includes(id)).length;
  return {
    checked: members.length > 0 && selected === members.length,
    indeterminate: selected > 0 && selected < members.length,
  };
}

function toggleAll() {
  selectedStoreIds.value = allSelected.value
    ? []
    : props.stores.map((store) => Number(store.id));
  ensureConfigs();
  void nextTick(refreshOptions);
}

function refreshOptions() {
  if (!props.open) return;
  ensureConfigs();
  emit("load-options", [...selectedStoreIds.value]);
}

function openMenu(event, store) {
  const id = Number(store.id);
  if (!selectedStoreIds.value.includes(id)) {
    selectedStoreIds.value = [id];
    ensureConfigs();
  }
  menu.currentStoreId = id;
  menu.selectedIds = [...selectedStoreIds.value];
  menu.x = Math.max(8, Math.min(event.clientX, window.innerWidth - 270));
  menu.y = Math.max(8, Math.min(event.clientY, window.innerHeight - 410));
  menu.open = true;
  batchPanel.open = false;
}

function closeMenu() {
  menu.open = false;
}

function selectCurrent() {
  if (menu.currentStoreId) {
    selectedStoreIds.value = [menu.currentStoreId];
    ensureConfigs();
  }
  closeMenu();
}

function selectAllPage() {
  selectedStoreIds.value = props.stores.map((store) => Number(store.id));
  ensureConfigs();
  closeMenu();
}

function selectAllFiltered() {
  selectedStoreIds.value = props.stores.map((store) => Number(store.id));
  ensureConfigs();
  closeMenu();
}

function invertSelection() {
  const all = props.stores.map((store) => Number(store.id));
  selectedStoreIds.value = all.filter((id) => !selectedStoreIds.value.includes(id));
  ensureConfigs();
  closeMenu();
}

function clearSelection() {
  selectedStoreIds.value = [];
  closeMenu();
}

async function copySelectedIds() {
  const ids = props.stores
    .filter((store) => menu.selectedIds.includes(Number(store.id)))
    .map((store) => Number(store.id));
  closeMenu();
  if (!ids.length) return;
  try {
    await copyText(ids.join("\n"));
    emit("toast", `已复制 ${ids.length} 家店铺编号`);
  } catch (cause) {
    emit("toast", cause instanceof Error ? cause.message : String(cause));
  }
}

function refreshMenuOptions() {
  emit("load-options", [...selectedStoreIds.value]);
  closeMenu();
}

function openBatchPanel(field) {
  batchPanel.open = true;
  batchPanel.field = field;
  batchPanel.label = fieldOptions(menu.currentStoreId, field)[0]?.label || "";
  closeMenu();
}

function applyBatchOption() {
  if (!batchPanel.field || !batchPanel.label) return;
  for (const store of selectedStores.value) {
    const match = fieldOptions(store.id, batchPanel.field).find(
      (item) => item.label === batchPanel.label,
    );
    if (!match) continue;
    const key = String(store.id);
    setConfig(store.id, batchPanel.field, match.id);
  }
  batchPanel.open = false;
  emit("toast", `已按名称统一设置 ${selectedStoreIds.value.length} 家店铺`);
}

function submit() {
  if (!canSubmit.value) return;
  ensureConfigs();
  emit("submit", {
    storeIds: [...selectedStoreIds.value],
    templateId: selectedTemplateId.value,
    mode: allocationMode.value,
    configs: Object.fromEntries(
      selectedStoreIds.value.map((id) => [
        String(id),
        {
          freight_template_id: configValue(id, "freight_template_id"),
          brand_id: configValue(id, "brand_id"),
          after_sale_address_id: configValue(id, "after_sale_address_id"),
        },
      ]),
    ),
  });
}

watch(() => props.open, (open) => {
  if (open) initialize();
  else {
    menu.open = false;
    batchPanel.open = false;
  }
});
watch(() => props.options, () => {
  if (props.open) ensureConfigs();
}, { deep: true });

onMounted(() => {
  outsideHandler = (event) => {
    if (!menu.open) return;
    if (
      event.target.closest?.(".upload-store-menu")
      || event.target.closest?.(".upload-store-row")
    ) return;
    closeMenu();
  };
  keyHandler = (event) => {
    if (event.key !== "Escape") return;
    if (batchPanel.open) {
      batchPanel.open = false;
      return;
    }
    if (menu.open) {
      closeMenu();
      return;
    }
    close();
  };
  document.addEventListener("pointerdown", outsideHandler, true);
  document.addEventListener("keydown", keyHandler);
});
onBeforeUnmount(() => {
  if (outsideHandler) {
    document.removeEventListener("pointerdown", outsideHandler, true);
  }
  if (keyHandler) document.removeEventListener("keydown", keyHandler);
});
</script>

<template>
  <div v-if="open" class="upload-store-overlay" @mousedown.self="close">
    <section class="upload-store-dialog" role="dialog" aria-modal="true" aria-labelledby="upload-store-title">
      <header class="upload-store-head">
        <div>
          <h2 id="upload-store-title">选择分配店铺</h2>
          <p>先选择店铺，再分别设置该店铺的运费模板、品牌和售后地址。</p>
        </div>
        <span>{{ selectedCountText }}</span>
      </header>

      <div class="upload-store-body">
        <div class="template-row">
          <label for="upload-template">上货模板</label>
          <select id="upload-template" v-model="selectedTemplateId" class="select">
            <option v-for="template in templates" :key="template.id" :value="template.id">
              {{ template.name }}{{ template.id === defaultTemplateId ? "（默认）" : "" }}
            </option>
          </select>
          <small>本次 {{ productCount }} 件商品</small>
        </div>

        <div class="mode-grid" role="radiogroup" aria-label="分配方式">
          <label class="mode-card" :class="{ active: allocationMode === 'average' }">
            <input v-model="allocationMode" type="radio" value="average" />
            <span>平均分配</span>
            <small>商品平均分配到所选店铺</small>
          </label>
          <label class="mode-card" :class="{ active: allocationMode === 'all' }">
            <input v-model="allocationMode" type="radio" value="all" />
            <span>每个店铺都上传</span>
            <small>每件商品都分配给所选店铺</small>
          </label>
        </div>

        <section class="store-section">
          <div class="section-title">
            <span>按分组选择</span>
            <small>{{ selectedCountText }}</small>
          </div>
          <div class="group-grid">
            <label v-for="group in groups" :key="group.name" class="group-card">
              <input
                type="checkbox"
                :checked="groupState(group).checked"
                :indeterminate="groupState(group).indeterminate"
                @change="toggleGroup(group)"
              />
              <span>{{ group.name }}</span>
              <small>{{ group.count }} 家</small>
            </label>
          </div>
        </section>

        <div class="loaded-line">
          已获取 {{ loadedCount }}/{{ selectedStoreIds.length }} 家店铺配置
          <span v-if="selectedCountText">{{ selectedCountText }}</span>
        </div>

        <label class="all-stores">
          <input
            type="checkbox"
            :checked="allSelected"
            :indeterminate="selectedStoreIds.length > 0 && !allSelected"
            @change="toggleAll"
          />
          <span>全选店铺</span>
        </label>

        <div class="store-list">
          <div
            v-for="store in stores"
            :key="store.id"
            class="upload-store-row"
            :class="{ selected: selectedStoreIds.includes(Number(store.id)) }"
            @contextmenu.prevent.stop="openMenu($event, store)"
          >
            <label class="store-identity">
              <input
                type="checkbox"
                :checked="selectedStoreIds.includes(Number(store.id))"
                @change="toggleStore(store.id)"
              />
              <span>
                <strong>{{ store.name }}</strong>
                <small>{{ store.group_name || "未分组" }} · {{ store.web_status || "状态未知" }}</small>
              </span>
            </label>

            <div v-if="selectedStoreIds.includes(Number(store.id))" class="store-config">
              <label class="field">
                <span>运费模板</span>
                <select
                  class="select"
                  :value="configValue(store.id, 'freight_template_id')"
                  @change="setConfig(store.id, 'freight_template_id', $event.target.value)"
                >
                  <option value="">来源或平台默认</option>
                  <option
                    v-for="option in fieldOptions(store.id, 'freight_template_id')"
                    :key="option.id"
                    :value="option.id"
                  >{{ option.label }}</option>
                </select>
              </label>
              <label class="field">
                <span>售后地址</span>
                <select
                  class="select"
                  :value="configValue(store.id, 'after_sale_address_id')"
                  @change="setConfig(store.id, 'after_sale_address_id', $event.target.value)"
                >
                  <option value="">自动选择默认地址</option>
                  <option
                    v-for="option in fieldOptions(store.id, 'after_sale_address_id')"
                    :key="option.id"
                    :value="option.id"
                  >{{ option.label }}</option>
                </select>
              </label>
              <label class="field brand-field">
                <span>品牌</span>
                <select
                  class="select"
                  :value="configValue(store.id, 'brand_id')"
                  @change="setConfig(store.id, 'brand_id', $event.target.value)"
                >
                  <option
                    v-for="option in fieldOptions(store.id, 'brand_id')"
                    :key="option.id"
                    :value="option.id"
                  >{{ option.label }}</option>
                </select>
              </label>
            </div>

            <div v-else class="store-config-placeholder">
              勾选后展开运费模板、品牌和售后地址
            </div>
          </div>
          <div v-if="!stores.length" class="store-empty">暂无可分配店铺</div>
        </div>

        <div class="qualification">
          <Icon name="package" :size="16" />
          本次资质图片会随上货模板一起校验
        </div>
      </div>

      <footer class="upload-store-foot">
        <button class="btn" type="button" :disabled="busy" @click="close">取消</button>
        <button class="btn blue" type="button" :disabled="!canSubmit" @click="submit">
          {{ busy ? "正在创建任务…" : "生成并开始上传" }}
        </button>
      </footer>

      <div
        v-if="menu.open"
        class="upload-store-menu"
        role="menu"
        :style="{ left: `${menu.x}px`, top: `${menu.y}px` }"
        @contextmenu.prevent.stop
      >
        <header>已选 {{ selectedStoreIds.length }} 家店铺</header>
        <button role="menuitem" type="button" @click="selectCurrent">选择当前项</button>
        <button role="menuitem" type="button" @click="selectAllPage">全选当前页</button>
        <button role="menuitem" type="button" @click="selectAllFiltered">全选筛选结果</button>
        <button role="menuitem" type="button" @click="invertSelection">反选</button>
        <button role="menuitem" type="button" @click="clearSelection">取消选择</button>
        <button role="menuitem" type="button" @click="refreshMenuOptions">刷新选中项</button>
        <button role="menuitem" type="button" @click="copySelectedIds">复制选中项编号</button>
        <hr />
        <button role="menuitem" type="button" @click="openBatchPanel('freight_template_id')">统一设置运费模板…</button>
        <button role="menuitem" type="button" @click="openBatchPanel('brand_id')">统一设置品牌…</button>
        <button role="menuitem" type="button" @click="openBatchPanel('after_sale_address_id')">统一设置售后地址…</button>
      </div>

      <div v-if="batchPanel.open" class="batch-config-layer" @mousedown.self="batchPanel.open = false">
        <section class="batch-config-card">
          <h3>
            {{ {
              freight_template_id: "统一设置运费模板",
              brand_id: "统一设置品牌",
              after_sale_address_id: "统一设置售后地址",
            }[batchPanel.field] }}
          </h3>
          <p>按中文名称匹配每家店铺的真实官方选项；找不到同名选项的店铺保持原配置。</p>
          <select v-model="batchPanel.label" class="select">
            <option v-for="option in batchOptions" :key="option.id" :value="option.label">
              {{ option.label }}
            </option>
          </select>
          <footer>
            <button class="btn" type="button" @click="batchPanel.open = false">取消</button>
            <button class="btn blue" type="button" :disabled="!batchPanel.label" @click="applyBatchOption">应用</button>
          </footer>
        </section>
      </div>
    </section>
  </div>
</template>

<style scoped>
.upload-store-overlay {
  position: fixed;
  inset: 0;
  z-index: 150;
  display: grid;
  place-items: center;
  padding: 22px;
  background: rgba(16, 30, 44, .48);
  backdrop-filter: blur(2px);
}

.upload-store-dialog {
  width: min(1180px, 100%);
  height: min(820px, calc(100vh - 38px));
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid #d5dfe9;
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 24px 72px rgba(16, 36, 55, .3);
}

.upload-store-head {
  min-height: 74px;
  padding: 14px 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  border-bottom: 1px solid #dce4ec;
  background: linear-gradient(180deg, #fbfdff, #f6f9fc);
}

.upload-store-head h2 {
  margin: 0;
  color: #17283a;
  font-size: 19px;
  font-weight: 600;
}

.upload-store-head p {
  margin: 4px 0 0;
  color: #758494;
  font-size: 11px;
}

.upload-store-head > span {
  padding: 5px 9px;
  border-radius: 99px;
  color: #567089;
  background: #edf2f7;
  font-size: 11px;
  white-space: nowrap;
}

.upload-store-body {
  min-height: 0;
  flex: 1;
  padding: 16px 20px;
  overflow: auto;
  background: #fff;
}

.template-row {
  display: grid;
  grid-template-columns: 112px minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  padding: 11px 12px;
  border: 1px solid #dce4ec;
  border-radius: 8px;
  background: #f7f9fc;
}

.template-row label {
  color: #34485c;
  font-weight: 600;
}

.template-row small {
  color: #83909e;
  font-size: 10px;
}

.mode-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}

.mode-card {
  min-height: 54px;
  padding: 9px 12px;
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr);
  align-items: center;
  gap: 2px 9px;
  border: 1px solid #dce4ec;
  border-radius: 8px;
  background: #fff;
  cursor: pointer;
}

.mode-card.active {
  border-color: #5b97ed;
  background: #f1f7ff;
  box-shadow: inset 0 0 0 1px #c7dcf8;
}

.mode-card input {
  grid-row: 1 / span 2;
}

.mode-card span {
  color: #2f4356;
  font-size: 12px;
  font-weight: 600;
}

.mode-card small {
  color: #8593a1;
  font-size: 10px;
}

.store-section {
  padding: 12px;
  border: 1px solid #dce4ec;
  border-radius: 8px;
  background: #f7f9fc;
}

.section-title {
  display: flex;
  justify-content: space-between;
  margin-bottom: 9px;
  color: #34485c;
  font-size: 12px;
  font-weight: 600;
}

.section-title small {
  color: #8593a1;
  font-weight: 400;
}

.group-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.group-card {
  min-height: 42px;
  padding: 7px 10px;
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid #dce4ec;
  border-radius: 7px;
  background: #fff;
  cursor: pointer;
}

.group-card span {
  flex: 1;
  color: #405468;
  font-size: 11px;
}

.group-card small {
  color: #8794a1;
  font-size: 10px;
  white-space: nowrap;
}

.loaded-line {
  margin: 11px 0;
  padding: 8px 10px;
  display: flex;
  justify-content: space-between;
  border: 1px solid #dce4ec;
  border-radius: 6px;
  color: #6c7c8c;
  background: #f8fafc;
  font-size: 10px;
}

.all-stores {
  min-height: 43px;
  padding: 0 8px;
  display: flex;
  align-items: center;
  gap: 10px;
  border: 1px solid #dce4ec;
  border-bottom: 0;
  border-radius: 8px 8px 0 0;
  color: #31475b;
  background: #fff;
  font-size: 12px;
  font-weight: 600;
}

.store-list {
  border: 1px solid #dce4ec;
  border-radius: 0 0 8px 8px;
  overflow: hidden;
}

.upload-store-row {
  min-height: 58px;
  padding: 10px 10px;
  display: grid;
  grid-template-columns: 250px minmax(0, 1fr);
  align-items: start;
  gap: 14px;
  border-bottom: 1px solid #e6ebf0;
  background: #fff;
  transition: background .15s ease;
}

.upload-store-row:last-child {
  border-bottom: 0;
}

.upload-store-row.selected {
  background: #eff7ff;
}

.store-identity {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 9px;
  cursor: pointer;
}

.store-identity span {
  min-width: 0;
  display: grid;
  gap: 3px;
}

.store-identity strong {
  overflow: hidden;
  color: #283d51;
  font-size: 12px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.store-identity small {
  color: #8492a0;
  font-size: 10px;
}

.store-config {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 12px;
}

.field {
  display: grid;
  grid-template-columns: 68px minmax(0, 1fr);
  align-items: center;
  gap: 8px;
}

.field > span {
  color: #6f8090;
  font-size: 10px;
  white-space: nowrap;
}

.brand-field {
  grid-column: 1 / -1;
}

.store-config-placeholder {
  align-self: center;
  color: #98a4b0;
  font-size: 10px;
}

.store-empty {
  padding: 60px;
  text-align: center;
  color: #8b98a4;
  font-size: 11px;
}

.qualification {
  margin-top: 12px;
  min-height: 54px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  border: 1px solid #c8ead6;
  border-radius: 7px;
  color: #27794a;
  background: #eff9f3;
  font-size: 11px;
}

.upload-store-foot {
  min-height: 62px;
  padding: 11px 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  border-top: 1px solid #dce4ec;
  background: #fbfcfe;
}

.upload-store-foot .btn {
  min-width: 138px;
}

.upload-store-menu {
  position: fixed;
  z-index: 180;
  width: 258px;
  max-height: 402px;
  padding: 5px;
  overflow-y: auto;
  border: 1px solid #cfdae5;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 16px 42px rgba(20, 42, 62, .25);
}

.upload-store-menu header {
  padding: 7px 9px;
  border-bottom: 1px solid #edf1f5;
  color: #8492a0;
  font-size: 9px;
}

.upload-store-menu button {
  width: 100%;
  min-height: 31px;
  padding: 0 9px;
  border: 0;
  border-radius: 4px;
  color: #3f5365;
  background: transparent;
  font-size: 11px;
  text-align: left;
}

.upload-store-menu button:hover {
  background: #edf5fe;
}

.upload-store-menu hr {
  margin: 4px 8px;
  border: 0;
  border-top: 1px solid #e3e9ee;
}

.batch-config-layer {
  position: fixed;
  inset: 0;
  z-index: 190;
  display: grid;
  place-items: center;
  background: rgba(19, 33, 47, .38);
}

.batch-config-card {
  width: min(480px, calc(100vw - 32px));
  padding: 18px;
  display: grid;
  gap: 12px;
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 20px 56px rgba(12, 32, 50, .32);
}

.batch-config-card h3 {
  margin: 0;
  color: #23394d;
  font-size: 15px;
}

.batch-config-card p {
  margin: 0;
  color: #71808f;
  font-size: 11px;
  line-height: 1.65;
}

.batch-config-card footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

@media (max-width: 820px) {
  .upload-store-overlay {
    padding: 0;
  }

  .upload-store-dialog {
    width: 100%;
    height: 100vh;
    border-radius: 0;
  }

  .upload-store-row {
    grid-template-columns: 1fr;
  }

  .group-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>

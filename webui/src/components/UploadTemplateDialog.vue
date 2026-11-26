<script setup>
import { computed, reactive, ref, watch } from "vue";
import { call } from "../api";
import {
  buildUploadTemplatePayload,
  validateLadderRules,
} from "../uploadTemplate";
import Icon from "./Icon.vue";
import ModalDialog from "./ModalDialog.vue";

const props = defineProps({
  open: Boolean,
  template: { type: Object, default: null },
  defaults: { type: Object, default: () => ({}) },
  violationTypes: { type: Array, default: () => [] },
  busy: Boolean,
});
const emit = defineEmits(["close", "save", "toast"]);

const form = reactive({});
const sourceTemplate = ref({});
const activeSection = ref("basic");
const priceOffsetYuan = ref("0");
const fixedPriceYuan = ref("0");
const validationMessage = ref("");
const folderBusy = ref(false);
const ladderOpen = ref(false);
const ladderRows = ref([]);
const cloudTypesOpen = ref(false);
const selectedCloudTypes = ref([]);

const isEditing = computed(() => Boolean(props.template?.id));
const listingLabel = computed(() => ({
  immediate: "出售中",
  warehouse: "仓库",
  draft: "草稿",
})[form.listing_mode] || "出售中");
const duplicateLabel = computed(() => ({
  store: "本店去重",
  computer: "本机去重",
  off: "关闭去重",
})[form.duplicate_filter_mode] || "本店去重");
const ladderCount = computed(() => (
  String(form.ladder_rules || "").split(/\r?\n/).filter((line) => line.trim()).length
));
const availableCloudTypes = computed(() => (
  [...new Set([
    ...props.violationTypes,
    ...String(form.cloud_violation_types || "").split(/\r?\n/),
  ].map((value) => String(value || "").trim()).filter(Boolean))]
));

function clone(value) {
  return JSON.parse(JSON.stringify(value ?? {}));
}

function hydrate() {
  const base = {
    name: "新上传模板",
    title_prefix: "",
    title_suffix: "",
    price_multiplier: 1,
    price_offset: 0,
    fixed_price_enabled: false,
    fixed_price: 0,
    final_price_decimals: 2,
    ladder_enabled: false,
    ladder_rules: "",
    stock: 1000,
    listing_mode: "immediate",
    duplicate_filter_mode: "store",
    category_business_type: "individual",
    video_folder: "",
    main_image_video: false,
    use_package_tags: true,
    read_packaging_labels: false,
    clothing_mode: false,
    seven_day_return: true,
    shipping_insurance: false,
    damage_return: false,
    fake_one_three: false,
    support_exchange: false,
    auto_open_category: true,
    force_fallback_category: false,
    distributor_name: "",
    distributor_contact: "",
    delivery_type: "现货",
    delivery_time: "48小时内发货",
    title_replace_rules: "",
    fallback_category: "",
    deposit_limit: 0,
    category_brand_ids: "",
    category_license_groups: "",
    low_price_enabled: false,
    low_price_multiplier: 2,
    low_price_action: "跳过商品",
    trim_head: 0,
    trim_detail: 0,
    cloud_violation: false,
    cloud_violation_types: "",
    strip_title_brackets: false,
    title_rules: "",
    sku_rules: "",
    image_mode: "默认方式",
    head_action: "删除违规图片",
    detail_action: "删除违规图片",
    image_rules: "",
    enable_custom_attributes: false,
    attribute_rules: "",
    ...clone(props.defaults),
    ...clone(props.template),
  };
  for (const key of Object.keys(form)) delete form[key];
  Object.assign(form, base);
  sourceTemplate.value = clone(props.template || {});
  priceOffsetYuan.value = (Number(base.price_offset || 0) / 100).toFixed(2);
  fixedPriceYuan.value = (Number(base.fixed_price || 0) / 100).toFixed(2);
  validationMessage.value = "";
  activeSection.value = "basic";
}

watch(
  () => props.open,
  (open) => {
    if (open) hydrate();
  },
);

async function selectVideoFolder() {
  if (folderBusy.value) return;
  folderBusy.value = true;
  try {
    const result = await call("upload.templates.select-folder", {
      initial_path: form.video_folder || "",
    });
    if (result.path) {
      form.video_folder = result.path;
      emit("toast", "已选择商品视频文件夹");
    }
  } catch (cause) {
    emit("toast", cause instanceof Error ? cause.message : String(cause));
  } finally {
    folderBusy.value = false;
  }
}

function openLadderEditor() {
  const lines = String(form.ladder_rules || "").split(/\r?\n/).filter((line) => line.trim());
  ladderRows.value = lines.length
    ? lines.map((line) => {
      const parts = line.split("=").map((item) => item.trim());
      return {
        low: parts[0] || "",
        high: parts[1] || "",
        multiplier: parts[2] || "",
        offset: parts[3] || "",
      };
    })
    : [{ low: "", high: "", multiplier: "1", offset: "0" }];
  validationMessage.value = "";
  ladderOpen.value = true;
}

function addLadderRow() {
  ladderRows.value.push({ low: "", high: "", multiplier: "1", offset: "0" });
}

function removeLadderRow(index) {
  if (ladderRows.value.length <= 1) return;
  ladderRows.value.splice(index, 1);
}

function saveLadderRules() {
  try {
    const text = ladderRows.value
      .map((row) => [
        String(row.low || "").trim(),
        String(row.high || "").trim(),
        String(row.multiplier || "").trim(),
        String(row.offset || "").trim(),
      ].join("="))
      .filter((line) => line !== "===")
      .join("\n");
    validateLadderRules(text);
    form.ladder_rules = text;
    ladderOpen.value = false;
  } catch (cause) {
    validationMessage.value = cause instanceof Error ? cause.message : String(cause);
  }
}

function openCloudViolationEditor() {
  selectedCloudTypes.value = String(form.cloud_violation_types || "")
    .split(/\r?\n/)
    .map((value) => value.trim())
    .filter(Boolean);
  cloudTypesOpen.value = true;
}

function saveCloudViolationTypes() {
  form.cloud_violation_types = selectedCloudTypes.value.join("\n");
  cloudTypesOpen.value = false;
}

function submit() {
  validationMessage.value = "";
  try {
    const payload = buildUploadTemplatePayload({
      form,
      sourceTemplate: sourceTemplate.value,
      priceOffsetYuan: priceOffsetYuan.value,
      fixedPriceYuan: fixedPriceYuan.value,
    });
    emit("save", payload);
  } catch (cause) {
    validationMessage.value = cause instanceof Error ? cause.message : String(cause);
  }
}
</script>

<template>
  <ModalDialog
    :open="open"
    :title="isEditing ? '编辑上货模板' : '添加上货模板'"
    subtitle="保存后只影响新任务，历史任务继续使用创建时的模板快照。"
    width="1080px"
    :busy="busy"
    @close="emit('close')"
  >
    <div class="template-editor">
      <div v-if="validationMessage" class="template-validation">{{ validationMessage }}</div>
      <nav class="template-tabs" aria-label="上货模板设置">
        <button type="button" :class="{ active: activeSection === 'basic' }" @click="activeSection = 'basic'">基础设置</button>
        <button type="button" :class="{ active: activeSection === 'filters' }" @click="activeSection = 'filters'">商品过滤</button>
        <button type="button" :class="{ active: activeSection === 'attributes' }" @click="activeSection = 'attributes'">属性设置</button>
      </nav>

      <div v-show="activeSection === 'basic'" class="template-scroll">
        <section class="template-section">
          <h3>模板</h3>
          <div class="template-grid one-wide">
            <label><span>模板名称</span><input v-model="form.name" class="input" /></label>
          </div>
        </section>

        <section class="template-section">
          <h3>发布与价格</h3>
          <div class="template-grid price-grid">
            <label>
              <span>默认发布算法 ×</span>
              <input v-model="form.price_multiplier" class="input" type="number" min="0" step="0.01" />
            </label>
            <label>
              <span>加价（元）</span>
              <input v-model="priceOffsetYuan" class="input" type="number" step="0.01" />
            </label>
            <label class="inline-check">
              <input v-model="form.fixed_price_enabled" type="checkbox" />
              <span>固定价格（元）</span>
            </label>
            <label>
              <span>固定价格</span>
              <input v-model="fixedPriceYuan" class="input" type="number" min="0" step="0.01" :disabled="!form.fixed_price_enabled" />
            </label>
            <label>
              <span>最终价格小数</span>
              <input v-model.number="form.final_price_decimals" class="input" type="number" min="0" max="4" step="1" />
            </label>
          </div>
          <div class="ladder-row">
            <label class="inline-check"><input v-model="form.ladder_enabled" type="checkbox" /><span>启用阶梯倍率</span></label>
            <button class="btn" type="button" @click="openLadderEditor">编辑阶梯倍率</button>
            <small>已设置 {{ ladderCount }} 条；未命中的原价使用上方默认算法。</small>
          </div>
        </section>

        <section class="template-section">
          <h3>标题处理</h3>
          <div class="template-grid">
            <label><span>标题前缀</span><input v-model="form.title_prefix" class="input" /></label>
            <label><span>标题后缀</span><input v-model="form.title_suffix" class="input" /></label>
          </div>
        </section>

        <section class="template-section">
          <h3>视频来源</h3>
          <div class="folder-row">
            <input v-model="form.video_folder" class="input" placeholder="文件夹内放商品ID.mp4，或按商品ID建子文件夹" />
            <button class="btn" type="button" :disabled="folderBusy" @click="selectVideoFolder">
              <Icon name="folder" :size="15" />{{ folderBusy ? "选择中…" : "选择文件夹" }}
            </button>
          </div>
        </section>

        <section class="template-section">
          <h3>服务与发布方式</h3>
          <div class="template-checks">
            <label><input v-model="form.main_image_video" type="checkbox" />主图生视频</label>
            <label><input v-model="form.use_package_tags" type="checkbox" />使用数据包标签</label>
            <label><input v-model="form.read_packaging_labels" type="checkbox" />读取包装标签图（较慢）</label>
            <label><input v-model="form.clothing_mode" type="checkbox" />服装模式（同色尺码统一最高价）</label>
            <label><input v-model="form.seven_day_return" type="checkbox" />支持七天无理由</label>
            <label><input v-model="form.shipping_insurance" type="checkbox" />支持运费险</label>
            <label><input v-model="form.damage_return" type="checkbox" />支持损坏包退</label>
            <label><input v-model="form.fake_one_three" type="checkbox" />支持假一赔三</label>
            <label><input v-model="form.support_exchange" type="checkbox" />支持换货</label>
            <label><input :checked="true" type="checkbox" disabled />目标类目未开通时强制自动申请并重试</label>
          </div>
          <div class="template-grid">
            <label>
              <span>上传后状态</span>
              <select v-model="form.listing_mode" class="select">
                <option value="immediate">出售中</option>
                <option value="warehouse">仓库</option>
                <option value="draft">草稿</option>
              </select>
              <small>{{ listingLabel }}：{{ form.listing_mode === "warehouse" ? "创建后下架" : form.listing_mode === "draft" ? "仅保存草稿" : "创建后直接出售" }}</small>
            </label>
            <label>
              <span>重复商品过滤</span>
              <select v-model="form.duplicate_filter_mode" class="select">
                <option value="store">本店去重</option>
                <option value="computer">本机去重</option>
                <option value="off">关闭去重</option>
              </select>
              <small>{{ duplicateLabel }}；关闭后即使上传过也会继续上传。</small>
            </label>
          </div>
        </section>

        <section class="template-section">
          <h3>标签图资料</h3>
          <div class="template-grid">
            <label><span>经销者名称</span><input v-model="form.distributor_name" class="input" /></label>
            <label><span>联系方式</span><input v-model="form.distributor_contact" class="input" /></label>
          </div>
        </section>

        <section class="template-section">
          <h3>发货设置</h3>
          <div class="template-grid three-wide">
            <label>
              <span>发货类型</span>
              <select v-model="form.delivery_type" class="select"><option>现货</option><option>预售</option></select>
            </label>
            <label>
              <span>发货时效</span>
              <select v-model="form.delivery_time" class="select">
                <option>48小时内发货</option><option>7天内发货</option><option>15天内发货</option>
              </select>
            </label>
            <label><span>统一库存</span><input v-model.number="form.stock" class="input" type="number" min="0" step="1" /></label>
          </div>
        </section>

        <section class="template-section">
          <h3>标题替换</h3>
          <p class="template-help">每行一条：旧标题片段=新标题片段；新标题片段留空表示删除。</p>
          <textarea v-model="form.title_replace_rules" class="input template-textarea" />
        </section>

        <section class="template-section">
          <h3>兜底类目</h3>
          <div class="template-grid">
            <label class="inline-check"><input v-model="form.force_fallback_category" type="checkbox" /><span>强制兜底</span></label>
            <label><span>兜底类目</span><input v-model="form.fallback_category" class="input" /></label>
            <label><span>最高保证金（元）</span><input v-model.number="form.deposit_limit" class="input" type="number" min="0" step="1" /></label>
            <label>
              <span>类目保证金业务类型</span>
              <select v-model="form.category_business_type" class="select">
                <option value="individual">个人店类目保证金</option>
                <option value="enterprise">企业店类目保证金</option>
              </select>
            </label>
            <label><span>申请品牌 ID</span><input v-model="form.category_brand_ids" class="input" placeholder="多个值按现有格式分隔" /></label>
            <label class="wide-field">
              <span>申请证照组 JSON</span>
              <input v-model="form.category_license_groups" class="input" placeholder='例如 [{"name":"营业执照","url":"..."}]' />
              <small>仅此字段保存 JSON 数组；为空表示不申请证照组。</small>
            </label>
          </div>
          <p class="template-help">
            默认保留数据包已有的官方类目；缺失时才使用可信的官方自动匹配。最高保证金仅用于合规候选排序和提示，不会在本地阻止上传，最终由微信平台申请与提交结果决定。保证金上限填 0 表示不限制。
          </p>
        </section>

        <section class="template-section">
          <h3>低价引流保护</h3>
          <div class="template-grid three-wide">
            <label class="inline-check"><input v-model="form.low_price_enabled" type="checkbox" /><span>启用保护</span></label>
            <label><span>最低价倍数</span><input v-model.number="form.low_price_multiplier" class="input" type="number" min="1" step="0.1" /></label>
            <label>
              <span>低价规格处理</span>
              <select v-model="form.low_price_action" class="select"><option>跳过商品</option><option>删除最低价</option></select>
            </label>
            <label><span>删除前主图</span><input v-model.number="form.trim_head" class="input" type="number" min="0" /></label>
            <label><span>删除前详情图</span><input v-model.number="form.trim_detail" class="input" type="number" min="0" /></label>
          </div>
        </section>
      </div>

      <div v-show="activeSection === 'filters'" class="template-scroll">
        <section class="template-section">
          <h3>违规商品过滤</h3>
          <div class="ladder-row">
            <label class="inline-check"><input v-model="form.cloud_violation" type="checkbox" /><span>启用云违规商品过滤</span></label>
            <button class="btn" type="button" @click="openCloudViolationEditor">云违规设置</button>
            <small>{{ String(form.cloud_violation_types || "").split(/\r?\n/).filter(Boolean).length }} 个类型；留空时按本机违规商品 ID 过滤。</small>
          </div>
          <label class="inline-check standalone-check"><input v-model="form.strip_title_brackets" type="checkbox" /><span>删除商品标题开头【】中的内容</span></label>
        </section>

        <section class="template-section">
          <h3>标题过滤</h3>
          <p class="template-help">每行一个违规词；标题命中后跳过该商品。</p>
          <textarea v-model="form.title_rules" class="input template-textarea" />
        </section>

        <section class="template-section">
          <h3>规格过滤</h3>
          <p class="template-help">每行一个违规词；任意规格命中后跳过该商品。</p>
          <textarea v-model="form.sku_rules" class="input template-textarea" />
        </section>

        <section class="template-section">
          <h3>图片过滤</h3>
          <div class="template-grid">
            <label>
              <span>模式</span>
              <select v-model="form.image_mode" class="select"><option>默认方式</option><option>OCR 识图</option></select>
            </label>
            <label>
              <span>主图命中</span>
              <select v-model="form.head_action" class="select"><option>删除违规图片</option><option>跳过商品</option></select>
            </label>
            <label>
              <span>详情图命中</span>
              <select v-model="form.detail_action" class="select"><option>删除违规图片</option><option>跳过商品</option></select>
            </label>
            <label><span>图片违规词</span><input v-model="form.image_rules" class="input" /></label>
          </div>
        </section>
      </div>

      <div v-show="activeSection === 'attributes'" class="template-scroll">
        <section class="template-section attribute-section">
          <h3>模板自定义属性</h3>
          <label class="inline-check"><input v-model="form.enable_custom_attributes" type="checkbox" /><span>启用模板自定义属性</span></label>
          <p class="template-help">数据包里的类目属性始终会提交；这里只追加模板属性。</p>
          <textarea v-model="form.attribute_rules" class="input template-attribute-area" />
        </section>
      </div>
    </div>
    <template #footer>
      <span class="template-footer-note">所有规则随模板快照保存，历史上传任务不会被后续修改覆盖。</span>
      <button class="btn" type="button" :disabled="busy" @click="emit('close')">取消</button>
      <button class="btn primary" type="button" :disabled="busy" @click="submit">{{ busy ? "保存中…" : "保存模板" }}</button>
    </template>
  </ModalDialog>

  <ModalDialog :open="ladderOpen" title="阶梯倍率" width="720px" :z-index="130" @close="ladderOpen = false">
    <div class="ladder-list">
      <div class="ladder-heading"><span>最低原价（元）</span><span>最高原价（元）</span><span>倍率</span><span>加价（元）</span><span /></div>
      <div v-for="(row, index) in ladderRows" :key="index" class="ladder-input-row">
        <input v-model="row.low" class="input" type="number" min="0" step="0.01" />
        <input v-model="row.high" class="input" type="number" min="0" step="0.01" />
        <input v-model="row.multiplier" class="input" type="number" min="0.01" step="0.01" />
        <input v-model="row.offset" class="input" type="number" step="0.01" />
        <button type="button" :disabled="ladderRows.length <= 1" @click="removeLadderRow(index)">删除</button>
      </div>
      <button class="btn" type="button" @click="addLadderRow">添加一行</button>
    </div>
    <p v-if="validationMessage" class="template-validation compact">{{ validationMessage }}</p>
    <template #footer>
      <button class="btn" type="button" @click="ladderOpen = false">取消</button>
      <button class="btn primary" type="button" @click="saveLadderRules">保存</button>
    </template>
  </ModalDialog>

  <ModalDialog :open="cloudTypesOpen" title="云违规设置" width="620px" :z-index="130" @close="cloudTypesOpen = false">
    <p class="template-help">选项来自本机违规中心“出售假冒商品”等真实违规原因，不使用样例数据。</p>
    <div v-if="availableCloudTypes.length" class="cloud-type-list">
      <label v-for="reason in availableCloudTypes" :key="reason">
        <input v-model="selectedCloudTypes" type="checkbox" :value="reason" />
        <span>{{ reason }}</span>
      </label>
    </div>
    <div v-else class="empty-cloud-types">违规中心尚未同步可识别的违规原因；留空时按本机违规商品 ID 过滤。</div>
    <template #footer>
      <button class="btn" type="button" @click="selectedCloudTypes = []">清空选择</button>
      <button class="btn" type="button" @click="cloudTypesOpen = false">取消</button>
      <button class="btn primary" type="button" @click="saveCloudViolationTypes">保存</button>
    </template>
  </ModalDialog>
</template>

<style scoped>
.template-editor { min-height: 0; display: flex; flex-direction: column; }
.template-validation {
  margin-bottom: 8px;
  padding: 8px 10px;
  border: 1px solid #edc5c9;
  border-radius: 5px;
  color: #a43f49;
  background: #fff6f7;
  font-size: 10px;
}
.template-validation.compact { margin: 8px 0 0; }
.template-tabs { display: flex; gap: 5px; border-bottom: 1px solid #dfe6eb; }
.template-tabs button { min-width: 104px; height: 36px; border: 1px solid transparent; border-bottom: 0; border-radius: 5px 5px 0 0; color: #586d7e; background: transparent; }
.template-tabs button.active { border-color: #d7e2e9; color: #176b4b; background: #fff; font-weight: 600; }
.template-scroll { height: min(67vh, 720px); padding: 12px 4px 4px 1px; overflow-y: auto; }
.template-section { margin-bottom: 11px; padding: 12px 13px; border: 1px solid #dfe7ec; border-radius: 7px; background: #fbfcfd; }
.template-section h3 { margin: 0 0 9px; color: #324c5f; font-size: 11px; }
.template-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 14px; }
.template-grid.one-wide { grid-template-columns: minmax(280px, 560px); }
.template-grid.three-wide { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.template-grid.price-grid { grid-template-columns: repeat(5, minmax(0, 1fr)); }
.template-grid label { display: grid; gap: 4px; color: #586b7b; font-size: 10px; }
.template-grid label > span { font-weight: 500; }
.template-grid small,
.template-help,
.ladder-row small { color: #8493a0; font-size: 9px; line-height: 1.5; }
.template-help { margin: 0 0 7px; }
.wide-field { grid-column: 1 / -1; }
.inline-check { display: flex !important; align-items: center; gap: 6px; align-self: end; min-height: 32px; }
.inline-check span { font-weight: 400; }
.standalone-check { margin-top: 10px; }
.ladder-row { margin-top: 11px; display: flex; align-items: center; gap: 9px; }
.ladder-row small { flex: 1; }
.folder-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; }
.template-checks { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 7px 12px; margin-bottom: 11px; }
.template-checks label,
.cloud-type-list label { display: flex; align-items: center; gap: 6px; color: #506675; font-size: 10px; }
.template-textarea { width: 100%; height: 92px; padding: 9px; resize: vertical; }
.template-attribute-area { width: 100%; height: 310px; padding: 9px; resize: vertical; }
.attribute-section { min-height: 390px; }
.template-footer-note { margin-right: auto; color: #81909c; font-size: 9px; }
.ladder-list { display: grid; gap: 7px; }
.ladder-heading,
.ladder-input-row { display: grid; grid-template-columns: 1fr 1fr 0.8fr 1fr 46px; gap: 7px; align-items: center; }
.ladder-heading { color: #728391; font-size: 9px; }
.ladder-input-row button { height: 28px; border: 0; color: #b34651; background: transparent; font-size: 9px; }
.cloud-type-list { max-height: 330px; padding: 4px 2px; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; overflow-y: auto; }
.empty-cloud-types { padding: 26px 12px; color: #82919d; background: #f7f9fa; text-align: center; font-size: 10px; }
@media (max-width: 900px) {
  .template-grid,
  .template-grid.price-grid,
  .template-grid.three-wide,
  .template-checks { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>

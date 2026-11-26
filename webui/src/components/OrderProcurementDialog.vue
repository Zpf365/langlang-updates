<script setup>
import {
  computed,
  onBeforeUnmount,
  ref,
  watch,
} from "vue";
import { call, formatMoney } from "../api";
import {
  confirmedPurchaseSummary,
  purchaseGate,
  scopePurchaseEvidenceToLinks,
} from "../orderProcurementFlow";
import {
  accountsForLink,
  chooseLinkAccount,
  detectProcurementPlatform,
  platformLabel,
  sourceProductId,
} from "../procurementLink";
import ModalDialog from "./ModalDialog.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  order: { type: Object, required: true },
});
const emit = defineEmits([
  "close",
  "changed",
  "toast",
  "request-address",
]);

const detail = ref(null);
const loading = ref(false);
const loadError = ref("");
const busyKey = ref("");
const links = ref([]);
const productPreview = ref(null);
const productPreviewStage = ref(0);
const draftSequence = ref(0);
const saveTimers = new Map();
const verificationJobs = ref(new Map());
const verificationTimers = new Map();

const items = computed(() => detail.value?.items || []);
const accounts = computed(() => (
  (detail.value?.procurement_accounts || []).filter((account) => (
    ["淘宝", "1688"].includes(String(account.platform || ""))
  ))
));
const products = computed(() => {
  const seen = new Set();
  return items.value.filter((item) => {
    const productId = String(item.product_id || "");
    if (!productId || seen.has(productId)) return false;
    seen.add(productId);
    return true;
  });
});
const previewCost = computed(() => 0);
const previewProfit = computed(
  () => Number(props.order.total_amount || 0) - previewCost.value,
);
const retainedPurchase = computed(() => confirmedPurchaseSummary(links.value));
const actualPurchaseProfit = computed(
  () => Number(props.order.total_amount || 0) - retainedPurchase.value.cost,
);
const recipient = computed(() => detail.value?.order?.address_info || {});
const addressReady = computed(
  () => Boolean(detail.value?.procurement_address_ready),
);
const stateLabel = computed(() => ({
  unlinked: "待关联",
  wait_purchase: "待拍单",
  wait_supplier: "自动监控上家发货",
  auto_filling: "自动回填物流",
  shipped: "店铺已发货",
  aftersale: "售后自动处理",
  error: "需人工核对",
}[props.order.procurement_state] || "待采购处理"));

function errorText(cause) {
  return cause instanceof Error ? cause.message : String(cause);
}

function notify(message) {
  emit("toast", String(message || ""));
}

function openProductPreview(item) {
  if (!item?.thumb_url) return;
  productPreview.value = {
    url: String(item.thumb_url),
    title: String(item.title || "订单商品"),
  };
  productPreviewStage.value = 0;
}

function cycleProductPreview() {
  if (productPreviewStage.value < 2) {
    productPreviewStage.value += 1;
  } else {
    productPreview.value = null;
    productPreviewStage.value = 0;
  }
}

function closeProductPreview() {
  productPreview.value = null;
  productPreviewStage.value = 0;
}

function itemKey(item) {
  return `${item.product_id || ""}:${item.sku_id || ""}`;
}

function itemForProduct(productId) {
  return items.value.find(
    (item) => String(item.product_id) === String(productId),
  ) || items.value[0] || {};
}

function productLabel(productId) {
  const item = itemForProduct(productId);
  return item.title || item.product_id || productId || "订单商品";
}

function accountLabel(accountId) {
  return accounts.value.find(
    (account) => String(account.id) === String(accountId),
  )?.label || "";
}

function normalizeSavedLink(link) {
  const platform = detectProcurementPlatform(link.source_url);
  const savedAccountId = Number(link.account_id || 0);
  const matchingAccount = accountsForLink(
    link.source_url,
    accounts.value,
  ).find((account) => Number(account.id) === savedAccountId);
  const account = matchingAccount || chooseLinkAccount(
    link.source_url,
    accounts.value,
    savedAccountId,
  );
  return {
    ...link,
    saved: true,
    platform,
    platform_label: platformLabel(platform),
    account_id: account?.id || "",
    account_label: account?.label || accountLabel(savedAccountId),
    url: link.source_url || "",
    source_product_id: link.source_product_id || sourceProductId(link.source_url),
    product_id: String(link.product_id || ""),
    purchase_attempt_status: String(link.purchase_attempt_status || ""),
    purchase_attempt_error: String(link.purchase_attempt_error || ""),
    purchase_opened_at: String(link.purchase_opened_at || ""),
    purchase_confirmed: Boolean(link.purchase_confirmed),
    purchase_record: link.purchase_record || null,
    _key: `saved-${link.id}`,
  };
}

function draftLink() {
  const productId = String(
    items.value[0]?.product_id
    || props.order.item_product_id
    || "",
  );
  draftSequence.value += 1;
  return {
    id: 0,
    saved: false,
    _key: `draft-${draftSequence.value}`,
    platform: "",
    platform_label: "",
    source_product_id: "",
    product_id: productId,
    account_id: "",
    account_label: "",
    url: "",
  };
}

function clearSaveTimer(key) {
  const timer = saveTimers.get(key);
  if (timer) clearTimeout(timer);
  saveTimers.delete(key);
}

async function loadDetail() {
  loading.value = true;
  loadError.value = "";
  try {
    const result = await call("orders.workbench.detail", {
      store_id: props.order.store_id,
      order_id: props.order.order_id,
    });
    applyDetail(result);
  } catch (cause) {
    loadError.value = errorText(cause);
  } finally {
    loading.value = false;
  }
}

function applyDetail(result) {
  if (!result) return;
  detail.value = result;
  const controllerProcurements = new Map(
    (result.procurement_links || [])
      .filter((row) => row.external_order_id)
      .map((row) => [String(row.external_order_id), row]),
  );
  links.value = scopePurchaseEvidenceToLinks(
    (result.product_purchase_links || [])
      .map(normalizeSavedLink),
  )
    .map((link) => {
      const record = link.purchase_record;
      if (!record) return link;
      const controllerRecord = controllerProcurements.get(
        String(record.upstream_order_id || ""),
      );
      return {
        ...link,
        purchase_record: {
          ...record,
          mail_no: record.mail_no
            || controllerRecord?.mail_no
            || "",
          status_text: record.status_text
            || controllerRecord?.status_text
            || "",
        },
      };
    });
}

function addLink() {
  const existingDraft = links.value.find((link) => !link.saved);
  if (existingDraft) return;
  links.value.push(draftLink());
}

function linkPlatform(link) {
  return detectProcurementPlatform(link.url);
}

function linkProductId(link) {
  return sourceProductId(link.url);
}

function canSaveLink(link) {
  return Boolean(linkPlatform(link) && linkProductId(link));
}

function linkGate(link) {
  return purchaseGate({
    url: link.url,
    accountId: link.account_id,
    accounts: accounts.value,
    addressReady: addressReady.value,
  });
}

function canOpenLink(link) {
  return Boolean(link.saved && linkGate(link).valid);
}

function purchaseStatusLabel(link) {
  const verification = verificationJobs.value.get(String(link.id));
  if (verification?.status === "waiting") {
    return "采购页已打开 / 等待付款，付款后自动核对";
  }
  if (verification?.status === "checking") {
    return verification.message || "自动核对中 / 稍后自动重试";
  }
  if (["pending", "error"].includes(verification?.status)) {
    return "采购结果待核实";
  }
  if (link.purchase_confirmed || link.purchase_attempt_status === "confirmed") {
    return "采购已确认";
  }
  if (link.purchase_attempt_status === "verification_pending") {
    return "采购结果待核实";
  }
  if (link.purchase_attempt_status === "page_open_pending_payment") {
    return "采购页面已打开 / 待用户付款";
  }
  if (link.purchase_attempt_status === "open_required") {
    return "请先完成官方采购付款";
  }
  if (link.purchase_attempt_status === "login_required") {
    return "采购账号登录已失效";
  }
  return "尚未打开采购页面";
}

function linkIssue(link) {
  if (link.saved) return linkGate(link).reason;
  const platform = linkPlatform(link);
  if (!platform) return "请输入有效的淘宝、天猫或 1688 商品链接";
  const options = accountsForLink(link.url, accounts.value);
  if (!options.length) {
    return `没有对应的${platformLabel(platform)}采购账号；链接可以保存，暂不能打开购买`;
  }
  if (!options.some((account) => String(account.status || "") === "已登录")) {
    return `${platformLabel(platform)}采购账号未登录；链接可以保存，暂不能打开购买`;
  }
  return "";
}

function onLinkUrlInput(link, value) {
  clearSaveTimer(link._key);
  link.url = String(value || "");
  link.platform = linkPlatform(link);
  link.platform_label = platformLabel(link.platform);
  link.source_product_id = linkProductId(link);
  link.account_id = chooseLinkAccount(
    link.url,
    accounts.value,
  )?.id || "";
  link.account_label = accountLabel(link.account_id);
  if (!link.saved && canSaveLink(link)) {
    saveTimers.set(
      link._key,
      setTimeout(() => {
        saveTimers.delete(link._key);
        persistLink(link, { automatic: true });
      }, 600),
    );
  }
}

function onProductChange(link, productId) {
  if (link.saved) return;
  link.product_id = String(productId || "");
}

function onAccountChange(link, accountId) {
  link.account_id = accountId;
  link.account_label = accountLabel(accountId);
  if (link.saved) persistLink(link);
}

function mergeSavedLink(row) {
  const normalized = normalizeSavedLink({
    ...row,
    id: Number(row.id),
    source_url: row.source_url,
  });
  const existingIndex = links.value.findIndex(
    (link) => link.saved && Number(link.id) === Number(normalized.id),
  );
  return { normalized, existingIndex };
}

async function persistLink(link, options = {}) {
  if (!canSaveLink(link)) {
    if (!options.automatic) notify("请输入有效的淘宝或 1688 商品链接");
    return;
  }
  if (busyKey.value) return;
  clearSaveTimer(link._key);
  busyKey.value = link._key;
  try {
    const result = await call("orders.procurement.link-upsert", {
      store_id: props.order.store_id,
      order_id: props.order.order_id,
      product_id: link.product_id || items.value[0]?.product_id || "",
      account_id: link.account_id || "",
      url: link.url.trim(),
    });
    const { normalized, existingIndex } = mergeSavedLink(result.link);
    const draftIndex = links.value.findIndex(
      (item) => item._key === link._key,
    );
    if (existingIndex >= 0 && draftIndex !== existingIndex) {
      links.value.splice(draftIndex, 1);
    } else if (draftIndex >= 0) {
      links.value.splice(draftIndex, 1, normalized);
    } else if (existingIndex >= 0) {
      links.value.splice(existingIndex, 1, normalized);
    } else {
      links.value.push(normalized);
    }
    notify(
      result.duplicate
        ? "该采购链接已保存，未重复添加"
        : "采购链接已保存",
    );
    emit("changed");
  } catch (cause) {
    notify(errorText(cause));
  } finally {
    busyKey.value = "";
  }
}

async function removeLink(link) {
  clearSaveTimer(link._key);
  if (!link.saved) {
    links.value = links.value.filter((item) => item._key !== link._key);
    return;
  }
  if (busyKey.value) return;
  busyKey.value = link._key;
  try {
    const result = await call("orders.procurement.link-delete", {
      store_id: props.order.store_id,
      order_id: props.order.order_id,
      product_id: link.product_id,
      link_id: Number(link.id),
    });
    detail.value = result.detail;
    links.value = links.value.filter(
      (item) => Number(item.id) !== Number(link.id),
    );
    notify("采购链接已删除");
    emit("changed");
  } catch (cause) {
    notify(errorText(cause));
  } finally {
    busyKey.value = "";
  }
}

async function openPurchase(link) {
  if (busyKey.value || !canOpenLink(link)) return;
  const gate = linkGate(link);
  if (!gate.valid) {
    notify(gate.reason);
    if (!addressReady.value && gate.platform && gate.account) {
      emit("request-address");
    }
    return;
  }
  link.account_id = gate.accountId;
  busyKey.value = link._key;
  try {
    const result = await call("orders.procurement.purchase-open", {
      store_id: props.order.store_id,
      order_id: props.order.order_id,
      product_id: link.product_id,
      link_id: Number(link.id),
      account_id: Number(gate.accountId),
    });
    link.purchase_attempt_status = String(result.status || "page_opened_pending_payment");
    link.purchase_opened_at = String(result.opened_at || "");
    if (result.verification_job_id) {
      startAutoVerification(link, result.verification_job_id);
    }
    notify(
      result.reused
        ? "该采购账号已打开此商品受控页面，请勿重复打开"
        : "采购商品页已打开；付款完成后软件会自动有限次核对，无需手动确认",
    );
  } catch (cause) {
    notify(errorText(cause));
  } finally {
    busyKey.value = "";
  }
}

function updateVerificationState(link, state) {
  const key = String(link.id);
  verificationJobs.value = new Map([
    ...verificationJobs.value,
    [key, state],
  ]);
}

function stopVerificationPolling(linkId) {
  const timer = verificationTimers.get(String(linkId));
  if (timer) clearTimeout(timer);
  verificationTimers.delete(String(linkId));
}

function startAutoVerification(link, jobId) {
  if (!jobId) return;
  stopVerificationPolling(link.id);
  let statusFailures = 0;
  updateVerificationState(link, {
    jobId,
    status: "waiting",
    message: "采购页已打开，等待付款完成",
  });

  const poll = async () => {
    try {
      const result = await call(
        "orders.procurement.purchase-auto-verify.status",
        { job_id: jobId },
      );
      statusFailures = 0;
      updateVerificationState(link, result);
      if (result.detail) applyDetail(result.detail);
      if (result.status === "confirmed") {
        stopVerificationPolling(link.id);
        emit("changed");
        notify("已自动回读并保存唯一匹配的采购订单");
        return;
      }
      if (["pending", "error"].includes(result.status)) {
        stopVerificationPolling(link.id);
        emit("changed");
        notify(
          result.reason
          || "自动核对未找到唯一采购订单，已标记待核实",
        );
        return;
      }
      const delay = result.status === "checking" ? 700 : 2500;
      verificationTimers.set(
        String(link.id),
        setTimeout(poll, delay),
      );
    } catch (cause) {
      statusFailures += 1;
      if (statusFailures < 3) {
        verificationTimers.set(
          String(link.id),
          setTimeout(poll, 1500),
        );
        return;
      }
      updateVerificationState(link, {
        jobId,
        status: "error",
        reason: errorText(cause),
        message: "自动核对状态读取失败",
      });
      stopVerificationPolling(link.id);
      notify(errorText(cause));
    }
  };
  verificationTimers.set(
    String(link.id),
    setTimeout(poll, 300),
  );
}

watch(
  () => props.open,
  (open) => {
    if (open) {
      loadDetail();
    } else {
      for (const key of saveTimers.keys()) clearSaveTimer(key);
    }
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  for (const key of saveTimers.keys()) clearSaveTimer(key);
  for (const linkId of verificationTimers.keys()) {
    stopVerificationPolling(linkId);
  }
});
</script>

<template>
  <ModalDialog
    :open="open"
    title="采购关联与打开购买"
    :subtitle="`${order.store_name} · ${order.order_id} · ${stateLabel}`"
    width="min(1580px, calc(100vw - 28px))"
    :busy="Boolean(busyKey)"
    @close="$emit('close')"
  >
    <div v-if="loading" class="procurement-loading">正在加载本地订单和已保存采购链接…</div>
    <div v-else-if="loadError" class="procurement-load-error">
      <strong>采购工作区加载失败</strong>
      <span>{{ loadError }}</span>
      <button class="btn" type="button" @click="loadDetail">重新读取</button>
    </div>
    <div v-else class="procurement-workspace">
      <aside class="procurement-order-rail">
        <section class="rail-card account-card">
          <small>当前采购账号</small>
          <strong>
            {{ accounts.filter((item) => item.status === "已登录").map((item) => item.label).join(" / ") || "无已登录账号" }}
          </strong>
          <span>账号切换暂不在本轮开放</span>
        </section>

        <section class="rail-card">
          <header>
            <strong>当前订单</strong>
            <span>{{ stateLabel }}</span>
          </header>
          <dl class="order-identity">
            <div><dt>店铺</dt><dd>{{ order.store_name }}</dd></div>
            <div><dt>订单号</dt><dd>{{ order.order_id }}</dd></div>
          </dl>
          <div class="local-order-items">
            <article
              v-for="item in items"
              :key="itemKey(item)"
              class="local-order-item"
            >
              <button
                v-if="item.thumb_url"
                class="product-thumb-button"
                type="button"
                title="点击放大商品图片"
                @click="openProductPreview(item)"
              >
                <img
                :src="item.thumb_url"
                alt=""
                referrerpolicy="no-referrer"
                />
              </button>
              <div>
                <strong>{{ item.title || "订单商品" }}</strong>
                <span>商品 ID：{{ item.product_id }}</span>
              </div>
              <div>
                <small>本地原始 SKU（只读）</small>
                <strong>{{ item.sku_attributes || "无规格文字" }}</strong>
                <span>SKU ID：{{ item.sku_id }}</span>
              </div>
              <div>
                <small>数量 / 售价</small>
                <strong>×{{ item.sku_count }}</strong>
                <span>{{ formatMoney(item.sale_price) }}</span>
              </div>
            </article>
          </div>
        </section>

        <section class="rail-card">
          <header><strong>收件信息</strong></header>
          <dl class="order-identity">
            <div><dt>收件人</dt><dd>{{ recipient.user_name || "—" }}</dd></div>
            <div><dt>电话</dt><dd>{{ recipient.tel_number || "—" }}</dd></div>
            <div><dt>地址</dt><dd>{{ [recipient.province_name, recipient.city_name, recipient.county_name, recipient.detail_info].filter(Boolean).join(" ") || "—" }}</dd></div>
          </dl>
        </section>

        <section class="rail-card finance-card">
          <div><span>订单金额</span><strong>{{ formatMoney(order.total_amount) }}</strong></div>
          <div><span>预计采购价</span><strong>{{ previewCost ? formatMoney(previewCost) : "待填写" }}</strong></div>
          <div><span>预计利润</span><strong>{{ previewCost ? formatMoney(previewProfit) : "—" }}</strong></div>
          <div v-if="retainedPurchase.count">
            <span>已采购实付</span>
            <strong>{{ formatMoney(retainedPurchase.paid) }}</strong>
          </div>
          <div v-if="retainedPurchase.count">
            <span>含运费成本</span>
            <strong>{{ formatMoney(retainedPurchase.cost) }}</strong>
          </div>
          <div v-if="retainedPurchase.count">
            <span>按实付利润</span>
            <strong>{{ formatMoney(actualPurchaseProfit) }}</strong>
          </div>
        </section>
      </aside>

      <section class="procurement-link-pane">
        <header class="pane-header">
          <div>
            <strong>采购链接与官方下单</strong>
            <small>多个链接可分别保存；付款完成后软件会自动有限次核对官方采购订单，以唯一匹配结果为唯一成功依据。</small>
          </div>
          <button class="btn" type="button" @click="addLink">添加采购链接</button>
        </header>
        <div v-if="!links.length" class="empty-links">
          暂无已保存采购链接。添加后可绑定同平台采购账号并打开官方商品页。
        </div>
        <div v-else class="procurement-link-list">
          <article
            v-for="link in links"
            :key="link._key"
            class="procurement-link-row"
          >
            <input
              :value="link.url"
              class="input link-url"
              :readonly="link.saved"
              placeholder="粘贴淘宝或1688商品链接"
              @input="onLinkUrlInput(link, $event.target.value)"
            />
            <select
              :value="link.product_id"
              class="select link-product"
              :disabled="link.saved"
              @change="onProductChange(link, $event.target.value)"
            >
              <option
                v-for="item in products"
                :key="item.product_id"
                :value="item.product_id"
              >
                {{ productLabel(item.product_id) }}
              </option>
            </select>
            <select
              :value="link.account_id"
              class="select link-account"
              @change="onAccountChange(link, $event.target.value)"
            >
              <option value="">未选择同平台账号</option>
              <option
                v-for="account in accountsForLink(link.url, accounts)"
                :key="account.id"
                :value="account.id"
              >
                {{ account.platform }} · {{ account.label }} · {{ account.status }}
              </option>
            </select>
            <div v-if="link.saved" class="link-actions">
              <button
                class="btn primary"
                type="button"
                :disabled="busyKey === link._key || !canOpenLink(link)"
                :title="linkGate(link).reason || '打开同平台已登录账号的官方采购页'"
                @click="openPurchase(link)"
              >
                {{ busyKey === link._key ? "处理中…" : (link.purchase_opened_at ? "重新打开购买" : "打开购买") }}
              </button>
            </div>
            <button
              v-else
              class="btn primary"
              type="button"
              :disabled="busyKey === link._key || !canSaveLink(link)"
              @click="persistLink(link)"
            >
              {{ busyKey === link._key ? "保存中…" : "保存链接" }}
            </button>
            <button class="btn danger link-delete" type="button" @click="removeLink(link)">
              删除
            </button>
            <p v-if="linkIssue(link)" class="link-reason">
              {{ linkIssue(link) }}
            </p>
            <p v-else class="link-binding-ok">
              {{ link.platform_label || platformLabel(linkPlatform(link)) }}
              · 商品 ID {{ link.source_product_id || linkProductId(link) }}
              · {{ link.account_label || accountLabel(link.account_id) || "未绑定账号" }}
              · {{ link.saved ? "已保存" : "待保存" }}
            </p>
            <p
              v-if="link.saved"
              class="purchase-status"
              :class="{ confirmed: link.purchase_confirmed }"
            >
              {{ purchaseStatusLabel(link) }}
              <span v-if="link.purchase_attempt_error"> · {{ link.purchase_attempt_error }}</span>
            </p>
            <div v-if="link.purchase_record" class="purchase-record">
              <div>
                <small>真实采购单</small>
                <strong class="mono">{{ link.purchase_record.upstream_order_id || "官方未返回" }}</strong>
              </div>
              <div>
                <small>实付 / 运费</small>
                <strong>
                  {{ formatMoney(link.purchase_record.paid_amount) }}
                  <span> + {{ formatMoney(link.purchase_record.freight_amount) }}</span>
                </strong>
              </div>
              <div>
                <small>上家物流</small>
                <strong class="mono">
                  {{ [link.purchase_record.mail_no, link.purchase_record.carrier].filter(Boolean).join(" · ") || "等待上家发货回读" }}
                </strong>
              </div>
              <div>
                <small>采购状态</small>
                <strong>{{ link.purchase_record.status_text || "官方暂未返回状态" }}</strong>
              </div>
              <div class="purchase-record-wide">
                <small>官方商品 / SKU</small>
                <strong>
                  {{ [link.purchase_record.goods_title, link.purchase_record.upstream_sku].filter(Boolean).join(" · ") || "官方暂未返回" }}
                </strong>
              </div>
            </div>
            <div class="address-gate">
              <span :class="{ ready: addressReady }">
                {{ addressReady ? "采购地址已就绪" : "采购地址已加密/不完整" }}
              </span>
              <button
                class="btn soft"
                type="button"
                @click="$emit('request-address')"
              >
                修改地址
              </button>
            </div>
          </article>
        </div>
      </section>
    </div>
    <template #footer>
      <span class="footer-note">前端不会代付，也不会把“页面已打开”当作“采购成功”；采购单、金额、物流和状态只采用 controller 官方回读结果。</span>
      <button class="btn" type="button" :disabled="Boolean(busyKey)" @click="$emit('close')">关闭</button>
    </template>
  </ModalDialog>
  <ModalDialog
    :open="Boolean(productPreview)"
    :title="productPreview?.title || '商品图片'"
    width="860px"
    @close="closeProductPreview"
  >
    <div
      class="product-preview-stage"
      :class="{ zoomed: productPreviewStage === 1 }"
      role="button"
      tabindex="0"
      :aria-label="productPreviewStage === 0 ? '点击放大' : productPreviewStage === 1 ? '点击缩小' : '点击隐藏图片'"
      @click="cycleProductPreview"
      @keydown.enter.prevent="cycleProductPreview"
      @keydown.space.prevent="cycleProductPreview"
    >
      <img
        v-if="productPreview"
        :src="productPreview.url"
        :alt="productPreview.title"
        referrerpolicy="no-referrer"
      />
      <span class="preview-hint">
        {{ productPreviewStage === 0 ? "点击放大" : productPreviewStage === 1 ? "点击缩小" : "点击隐藏" }}
      </span>
    </div>
    <template #footer>
      <button class="btn" type="button" @click="closeProductPreview">关闭</button>
    </template>
  </ModalDialog>
</template>

<style scoped>
.procurement-loading { min-height: 220px; display: grid; place-items: center; color: #748394; }
.procurement-load-error { min-height: 260px; display: grid; place-content: center; justify-items: center; gap: 9px; color: #9b3740; text-align: center; }
.procurement-load-error strong { color: #822a32; font-size: 13px; }
.procurement-load-error span { max-width: 620px; font-size: 11px; line-height: 1.65; }
.procurement-workspace { min-height: 650px; display: grid; grid-template-columns: minmax(300px, 340px) minmax(0, 1fr); gap: 12px; }
.procurement-order-rail { position: sticky; top: 0; align-self: start; display: grid; gap: 9px; max-height: 690px; overflow: auto; }
.rail-card { padding: 10px; display: grid; gap: 8px; border: 1px solid #dce4eb; border-radius: 8px; background: #fff; }
.rail-card > header { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.rail-card > header strong, .pane-header strong { color: #263f52; font-size: 12px; }
.rail-card > header span, .rail-card > small { color: #7c8c99; font-size: 10px; }
.rail-card > strong { color: #21435b; font-size: 12px; }
.account-card { background: linear-gradient(135deg, #f4f8fb, #edf4f8); }
.order-identity { margin: 0; display: grid; gap: 5px; }
.order-identity > div { display: grid; grid-template-columns: 58px minmax(0, 1fr); gap: 7px; font-size: 10px; }
.order-identity dt { color: #80909c; }
.order-identity dd { margin: 0; overflow-wrap: anywhere; color: #314b5e; }
.finance-card > div { display: flex; align-items: center; justify-content: space-between; font-size: 10px; }
.finance-card span { color: #7c8c99; }
.finance-card strong { color: #243e51; }
.procurement-link-pane { min-width: 0; display: grid; align-content: start; border: 1px solid #dce4eb; border-radius: 8px; overflow: hidden; }
.pane-header { min-height: 50px; padding: 9px 12px; display: flex; align-items: center; justify-content: space-between; gap: 10px; border-bottom: 1px solid #e3e9ef; background: #f7f9fb; }
.pane-header > div { display: grid; gap: 3px; }
.pane-header small { color: #81909d; font-size: 10px; }
.procurement-section { border: 1px solid #dce4eb; border-radius: 8px; overflow: hidden; }
.local-order-items { padding: 8px; display: grid; gap: 7px; }
.local-order-item { min-height: 70px; padding: 8px 10px; display: grid; grid-template-columns: 48px minmax(0, 1fr); gap: 9px; align-items: center; border: 1px solid #e1e7ed; border-radius: 7px; background: #fff; }
.local-order-item img { width: 42px; height: 42px; object-fit: cover; border-radius: 6px; background: #eef2f5; }
.product-thumb-button { width: 42px; height: 42px; padding: 0; border: 0; border-radius: 6px; overflow: hidden; background: #eef2f5; cursor: zoom-in; }
.product-thumb-button:hover img { transform: scale(1.04); }
.product-thumb-button img { display: block; transition: transform 0.18s ease; }
.local-order-item > div { min-width: 0; display: grid; gap: 3px; grid-column: 2; }
.local-order-item strong { overflow: hidden; color: #25394b; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.local-order-item span, .local-order-item small { overflow: hidden; color: #7b8996; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.procurement-link-list { padding: 8px; display: grid; gap: 7px; }
.procurement-link-row { min-width: 0; padding: 10px; display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) 112px 72px; gap: 8px; border: 1px solid #e1e7ed; border-radius: 7px; background: #fbfcfd; }
.procurement-link-row > * { min-width: 0; }
.link-url { grid-column: 1 / 3; width: 100%; }
.link-product, .link-account { width: 100%; }
.link-actions { grid-column: 3 / 4; }
.link-delete { grid-column: 4 / 5; padding: 0 10px; white-space: nowrap; }
.link-actions { display: grid; gap: 5px; }
.link-match-fields { grid-column: 1 / -1; display: grid; grid-template-columns: 180px 220px minmax(0, 1fr); gap: 7px; align-items: end; padding-top: 3px; }
.link-match-fields label { display: grid; gap: 3px; color: #6f7f8c; font-size: 9px; }
.link-match-fields > span { padding-bottom: 7px; color: #84929e; font-size: 9px; }
.procurement-link-row .link-reason, .procurement-link-row .link-binding-ok, .procurement-link-row .purchase-status { grid-column: 1 / -1; margin: 0; font-size: 10px; }
.procurement-link-row .link-reason { color: #a33b35; }
.procurement-link-row .link-binding-ok { color: #43725c; }
.procurement-link-row .purchase-status { color: #7a5a16; }
.procurement-link-row .purchase-status.confirmed { color: #216b48; }
.purchase-record {
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 7px;
  padding: 9px;
  border: 1px solid #cfe5da;
  border-radius: 7px;
  background: #f3faf6;
}
.purchase-record > div { min-width: 0; display: grid; gap: 3px; }
.purchase-record small { color: #6f897b; font-size: 9px; }
.purchase-record strong { overflow: hidden; color: #235a43; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.purchase-record strong span { color: #6f897b; font-weight: 400; }
.purchase-record-wide { grid-column: 1 / -1; }
.address-gate { grid-column: 1 / -1; display: flex; align-items: center; justify-content: space-between; gap: 8px; padding-top: 4px; border-top: 1px dashed #e1e7ed; }
.address-gate span { color: #a33b35; font-size: 10px; }
.address-gate span.ready { color: #43725c; }
.empty-links { padding: 22px; color: #7b8996; font-size: 11px; text-align: center; }
.footer-note { margin-right: auto; color: #7b8996; font-size: 10px; }
.procurement-link-row .address-gate { grid-column: 1 / -1; }
.product-preview-stage { position: relative; min-height: 460px; max-height: 76vh; display: grid; place-items: center; overflow: auto; border-radius: 8px; background: #f3f6f8; cursor: zoom-in; }
.product-preview-stage img { max-width: 100%; max-height: 72vh; object-fit: contain; transition: transform 0.2s ease; }
.product-preview-stage.zoomed { cursor: zoom-out; }
.product-preview-stage.zoomed img { max-width: none; max-height: none; transform: scale(1.65); }
.preview-hint { position: sticky; left: 50%; bottom: 10px; padding: 5px 10px; border-radius: 999px; color: #fff; background: rgba(27, 45, 59, 0.72); font-size: 10px; pointer-events: none; }
@media (max-width: 1180px) {
  .procurement-workspace { grid-template-columns: 1fr; }
  .procurement-order-rail { position: static; max-height: none; }
  .procurement-link-row { grid-template-columns: minmax(0, 1fr) 92px; }
  .link-url, .link-product, .link-account { grid-column: 1 / -1; }
  .link-actions { grid-column: 1 / 2; }
  .link-delete { grid-column: 2 / 3; }
  .purchase-record { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>

<script setup>
defineProps({
  display: { type: Object, required: true },
});
</script>

<template>
  <div
    class="store-sales-value"
    :class="display.showRefund ? 'double-line' : 'single-line'"
    :title="display.title"
  >
    <span
      class="store-sale-line sale-tone"
      :class="{ unknown: display.salesUnknown }"
    >{{ display.salesText }}</span>
    <span
      v-if="display.showRefund"
      class="store-refund-line refund-tone"
      :class="{ unknown: display.refundUnknown }"
    >{{ display.refundText }}</span>
  </div>
</template>

<style scoped>
.store-sales-value {
  width: 100%;
  min-height: 40px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 1px;
  line-height: 1.15;
  font-variant-numeric: tabular-nums;
}

.store-sale-line,
.store-refund-line {
  display: block;
  white-space: nowrap;
  font-size: var(--store-table-sales-double-font-size, 11px);
}

.single-line .store-sale-line {
  font-size: var(--store-table-sales-single-font-size, 14px);
  font-weight: 600;
}

.sale-tone {
  color: var(--store-sales-color, #466d5c);
}

.refund-tone {
  color: var(--store-refund-color, #8a5058);
}

.unknown {
  color: var(--store-table-unknown-color, #929aa2);
  font-weight: 400;
}
</style>

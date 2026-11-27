<script setup>
defineProps({
  display: { type: Object, required: true },
});
</script>

<template>
  <span
    class="store-exposure-value"
    :class="{ unknown: display.unknown }"
    :title="display.title"
  >{{ display.text }}</span>
</template>

<style scoped>
.store-exposure-value {
  display: inline-block;
  white-space: nowrap;
  color: #4a5d6c;
  font-variant-numeric: tabular-nums;
}

.store-exposure-value.unknown {
  color: var(--store-table-unknown-color, #929aa2);
}
</style>

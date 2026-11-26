<script setup>
import Icon from "./Icon.vue";

defineProps({
  open: Boolean,
  title: { type: String, default: "" },
  subtitle: { type: String, default: "" },
  width: { type: String, default: "720px" },
  panelClass: { type: String, default: "" },
  zIndex: { type: Number, default: 100 },
  confirmLabel: { type: String, default: "" },
  cancelLabel: { type: String, default: "取消" },
  busy: Boolean,
});
defineEmits(["close", "confirm", "cancel"]);
</script>

<template>
  <Teleport to="body">
    <Transition name="modal">
      <div
        v-if="open"
        class="modal-mask"
        :style="{ zIndex }"
        role="presentation"
        @mousedown.self="$emit('close')"
      >
        <section
          class="modal-card"
          :class="panelClass"
          :style="{ maxWidth: width }"
          role="dialog"
          aria-modal="true"
        >
          <header class="modal-header">
            <div>
              <h2>{{ title }}</h2>
              <p v-if="subtitle">{{ subtitle }}</p>
            </div>
            <button class="icon-button" type="button" @click="$emit('close')">
              <Icon name="close" :size="20" />
            </button>
          </header>
          <div class="modal-body">
            <slot />
          </div>
          <footer
            v-if="$slots.footer || confirmLabel || cancelLabel"
            class="modal-footer"
          >
            <slot name="footer">
              <button
                v-if="cancelLabel"
                class="btn"
                type="button"
                :disabled="busy"
                @click="$emit('close')"
              >
                {{ cancelLabel }}
              </button>
              <button
                v-if="confirmLabel"
                class="btn primary"
                type="button"
                :disabled="busy"
                @click="$emit('confirm')"
              >
                {{ confirmLabel }}
              </button>
            </slot>
          </footer>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

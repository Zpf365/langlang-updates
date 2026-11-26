<script setup>
import Icon from "../components/Icon.vue";
</script>

<template>
  <section class="page">
    <header class="page-header">
      <div>
        <h1>中转订单</h1>
        <p>偏远地区中转批量配置正在验收，本版本暂时冻结。</p>
      </div>
    </header>
    <div class="card paused-card">
      <Icon name="truck" :size="28" />
      <strong>启动兼容修复版本</strong>
      <p>本版本只修复旧数据库升级后的启动错误，不包含尚未验收的中转批量写入功能。</p>
    </div>
  </section>
</template>

<style scoped>
.page-header p {
  margin: 4px 0 0;
  color: #7b8b98;
  font-size: 12px;
}

.paused-card {
  min-height: 260px;
  display: grid;
  place-items: center;
  align-content: center;
  gap: 10px;
  color: #6d7e8b;
  text-align: center;
}

.paused-card strong {
  color: #2d4659;
  font-size: 15px;
}

.paused-card p {
  max-width: 560px;
  margin: 0;
  line-height: 1.7;
}
</style>

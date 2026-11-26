# 澜琅 1.8.98.11 内部候选

本版本为紧急启动兼容修复，不发布 GitHub。

## 修复

- 修复旧数据库升级后启动时报
  `sqlite3.OperationalError: no such column: business_key` 的问题。
- 在创建 `analytics_outbox` 相关索引之前，先检查并补齐
  `business_key`、`aggregation`、`revision`、`last_error_category`。
- 旧 Outbox 数据保留，不删除数据库、不重建表、不清理历史事件。
- 新增旧版数据库升级回归测试。
- 修复跨自然日后“今日销售”沿用上一日销售数值的问题。
- 销售缓存增加自然日快照；新一天官方实时接口暂不可用时显示本地订单口径，
  不再把昨日金额和单量继续放进今日。

## 暂缓

- 偏远地区中转批量开通与运费模板编辑功能本轮冻结隐藏。
- 偏远中转批量配置待启动与销售口径修复验收后再恢复。

## 验证范围

- 定向数据库迁移测试。
- Production WebUI build。
- PyInstaller 与候选 EXE 启动验证。

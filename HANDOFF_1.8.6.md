# 澜琅 重构1.8.6 交接

## 发布属性

- 版本：`1.8.6`
- 类型：正式自动更新版

## 本版修复

- `upload_tasks` 和 `upload_batch` 只保存本机 SQLite。
- `upload_last_source` 保持本机路径，不作为共享设置。
- 组织共享设置拉取时跳过上述设备专属大字段。
- 已删除正式组织设置中的 `upload_tasks`、`upload_batch` 历史大字段。
- 上传任务进度更新不会再触发 PostgreSQL statement timeout。

## 验证

- 2 MB 上传任务快照本地保存测试。
- 组织设置拉取忽略上传任务大字段测试。
- 上传表格缺失/重复编号、混合价格库存和自动选中测试。
- 完整回归、静态检查、构建、EXE 和 ZIP 启动。

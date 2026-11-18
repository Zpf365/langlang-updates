# 澜琅 重构1.8.6 测试报告

## 超时修复验证

- 2 MB `upload_tasks` 快照只写入本机，不调用云端设置 RPC。
- 组织设置同步不会拉回 `upload_tasks`、`upload_batch` 和本机来源路径。
- 云端历史超大设置字段已清理。

## 保留功能

- 上传表格修复缺失或重复商品编号。
- 混合价格、库存类型不会清空表格。
- 新导入商品自动选中。
- 商品页缓存、分组查询和违规同步保持 1.8.5 行为。

## 发布验证

完整回归：

```text
302 passed, 2 skipped, 1 warning, 12 subtests passed
```

按钮审计：

```text
FINDINGS=338
UNCOVERED=0
```

Ruff、compileall、PyInstaller、EXE 启动和发行 ZIP 启动通过。
最终 SHA256 以发行目录中的 `SHA256SUMS_1_8_6.txt` 为准。

# 澜琅 1.8.27 测试报告

## 编译

```text
python -m compileall -q weimi_manager tests
```

结果：通过。

## 静态检查

本版修改文件：

```text
ruff check weimi_manager/models.py weimi_manager/db.py
  weimi_manager/dashboard_metrics.py weimi_manager/ui/pages.py
  weimi_manager/ui/order_workbench.py
  tests/test_tuoke_replica_workbench.py
```

结果：通过。

## 针对性测试

```text
pytest tests/test_tuoke_replica_workbench.py tests/test_core.py -q
```

结果：`68 passed`。

## 全量回归

```text
pytest -q
```

结果：`390 passed, 6 skipped, 18 subtests passed`。

只有一条已有 PytestCollectionWarning：`TesterAuthService` 因带构造函数未被当作测试类收集。

## 界面实测

使用独立预览数据库启动源码版 1.8.27，实际打开并检查：

- 订单管理：未领取礼物单未进入列表。
- 订单工作台：店铺售后、内部中转、利润统计三视图和数据大屏均可打开。
- 数据大屏：有效订单 1 单、退款中 1 单，未领取礼物单未计入。

预览数据库路径：

```text
D:\GPT\artifacts\langlang-ui-preview-1.8.27\weimi_manager.db
```

## 未执行

- 未执行生产店铺售后同意、拒绝、退款、发货或采购写操作。
- 未连接真实生产店铺验证平台写操作。

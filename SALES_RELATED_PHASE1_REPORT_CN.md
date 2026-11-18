# 动销相关第一阶段实施报告

## 实施范围

本阶段只实现本地规则、数据持久化和状态机，不调用真实下单、付款或退款接口。

新增模块：

- `weimi_manager/sales_related/`

主要能力：

- TXT/CSV 文本链接导入，不设置 50 条硬编码上限。
- 相同链接去重，并支持显式允许重复任务。
- 任意、最低价、最高价、指定 SKU/规格、商品默认 SKU 五种选择方式。
- 购买数量支持 1、3、5、10 等自定义正整数。
- 数量必须有单个 SKU 一次满足，禁止因库存不足自动拆单。
- 金额统一使用整数分。
- 任务 `task_id`、`submission_id` 和最多一笔订单约束。
- 提交结果未知状态及可信订单核验要求。
- 设备认证哈希、任务租约、心跳、释放和过期恢复基础。
- 买家白名单、金额上限、每日/月累计上限、有效期和紧急停用规则。
- 售后申请、商家同意、支付受理和退款成功状态分离。
- 不可修改、不可删除的本地审计日志。

## 数据库迁移

新增表：

- `schema_migrations`
- `sales_tasks`
- `sales_task_sku_snapshots`
- `sales_orders`
- `sales_order_verifications`
- `sales_devices`
- `sales_device_leases`
- `sales_buyer_whitelist`
- `sales_aftersale_cases`
- `sales_audit_logs`

迁移特性：

- 独立版本号为 `sales_related: 1`。
- 旧数据库存在店铺数据时，迁移前生成
  `*.pre-sales-related-v1.bak` 完整备份。
- 迁移使用事务执行并检查所有必需表。
- 重复启动不会重复迁移或重复生成同一阶段备份。
- 不修改现有采购中心、订单和售后表。

## 订单安全边界

- 未取得 `submission_id` 的任务不能进入提交阶段。
- Android 或其他客户端回传不能单独作为可信订单来源。
- 只有 `merchant_order_api` 或明确标记的模拟可信来源可以写入已验证订单。
- `sales_orders.task_id` 唯一，禁止一个采购任务拆成多笔订单。
- 提交结果未知时进入 `unknown`，核实后才能恢复。

## 售后边界

- 仅 `openid` 可作为默认自动匹配的已验证买家标识类型。
- 昵称、姓名和手机号缺失或不可验证时转人工。
- 当前只输出“模拟通过”“人工复核”或“拒绝”规则结果。
- 不发送任何真实退款请求。

## 测试结果

针对性测试：

```text
34 passed
```

全量回归最终结果：

```text
345 passed, 1 failed, 7 skipped, 18 subtests passed
```

唯一失败项：

```text
tests/test_package_manifest.py::test_release_source_has_public_auth_config
```

失败原因是源码交付包不包含真实的 `assets/auth_config.json`，与动销相关模块无关。

排除该项发布配置检查后，全量回归为：

```text
346 passed, 5 skipped, 18 subtests passed
```

Ruff 已对全部新增模块和测试通过。现有旧代码的 Ruff 基线问题没有在本阶段扩大处理。

## 尚未实现

- 电脑端七个业务页签。
- 云端统一任务队列和 Supabase 表/函数。
- 安卓 Kotlin 工程。
- 真实微信商品下单、付款和退款接口。
- 真实测试店铺和安卓实机验收。

# 澜琅 1.6.51 Audit Round 1 修复报告

## 基线

- Git 基线标签：`baseline-1.6.51`
- 修复分支：`fix/audit-round1`
- 基线提交：`ce5c062`
- 基线安全快照：`D:\GPT\澜琅_1.6.51_Git基线安全快照.zip`
- 版本号：`1.6.51`，本轮未修改、未发布
- 测试：原 112 项 + 新增 20 项，共 132 项通过
- Windows 冒烟：主窗口测试和网页登录店铺弹窗测试通过

## LL-001

- 原文件和函数：`weimi_manager/ui/pages.py` 的 `OrdersPage.refresh()`
- 修改文件：`weimi_manager/ui/pages.py`、`tests/test_audit_round1.py`
- 原因：`refresh()` 调用了不存在的 `_query_background()`，实际触发 `AttributeError`
- 修复：恢复调用现有 `_query()`，保留原订单筛选、排序和渲染路径
- 风险或兼容说明：低风险；LL-010 阶段再迁移为数据库分页异步查询
- 新增测试：刷新时重新加载店铺并执行订单查询
- 提交：`0f6fc7c`
- 需要用户确认：否

## LL-002

- 原文件和函数：`weimi_manager/procurement.py` 的 `normalize_procurement_records()`、`_int()`
- 修改文件：`weimi_manager/procurement.py`、`tests/test_audit_round1.py`
- 原因：金额通过 `float()` 和 `round()` 转换，且淘宝、1688 字段未区分来源单位
- 修复：新增 `Decimal` 金额解析，统一输出整数分；记录金额来源字段和单位
- 风险或兼容说明：已有数据库金额不自动换算；重开数据库测试确认旧值保持原样
- 新增测试：淘宝小数金额、1688 整数分、旧金额重开数据库后保持不变
- 提交：`6c80bd7`、`f776521`
- 需要用户确认：是；平台字段单位映射仍需结合真实脱敏样本复核

## LL-003

- 原文件和函数：`weimi_manager/db.py` 的 `procurement_links` Schema、`upsert_procurement_link()`
- 修改文件：`weimi_manager/db.py`、`tests/test_audit_round1.py`
- 原因：唯一约束缺少 `store_id`，第二家店铺关联相同订单号时会覆盖第一家
- 修复：唯一键改为 `(store_id, order_id, procurement_order_id)`；旧表在事务内重建
- 风险或兼容说明：迁移前生成 `*.pre-ll003.bak`；迁移校验行数，失败自动回滚；已验证 `integrity_check` 和外键检查
- 新增测试：跨店铺同订单号、旧表迁移、数据保留、完整性检查
- 提交：`e362299`、`8c076eb`
- 需要用户确认：是；首次启动旧数据库时会执行一次自动迁移

## LL-004

- 原文件和函数：`Database.add_store()`、`upsert_stores()`、`StoreDialog._save()`、`StoreBatchDialog._submit()`
- 修改文件：`weimi_manager/db.py`、`service.py`、`web_session.py`、`ui/dialogs.py`、`ui/store_page.py`、`tests/test_audit_round1.py`
- 原因：手工添加店铺被强制要求 AppSecret；底层加密器也拒绝空字符串
- 修复：增加“仅网页登录”选项；空 AppSecret 使用空 BLOB 哨兵，真实密钥继续强制 DPAPI；网页登录不调用依赖密钥的公开 API
- 风险或兼容说明：空白批量导入不会清除已有真实 AppSecret；新增批量行解析纯函数，避免把店铺名称误当密钥
- 新增测试：空密钥店铺、保留既有密钥、API 调用明确拒绝、批量行解析
- 提交：`b3c7156`、`2aa19ed`
- 需要用户确认：是；新增了“仅网页登录”复选框，未删除原有字段

## LL-005

- 原文件和函数：`weimi_manager/procurement.py` 的 `normalize_procurement_records()`
- 修改文件：`weimi_manager/procurement.py`、`tests/test_audit_round1.py`
- 原因：仅有 `itemName`、通用 `code` 或取件码的字典也会生成采购记录
- 修复：按平台订单号字段和运单号字段验证；不再用任意字典哈希伪造订单号
- 风险或兼容说明：纯取件码/纯物流码响应会停止入库，需要后续用真实脱敏响应确认是否属于完整订单
- 新增测试：标题单独、通用 code 单独被拒绝；平台订单号有效时正常解析
- 提交：`35a1ae6`
- 需要用户确认：是

## LL-006

- 原文件和函数：`weimi_manager/ui/pages.py` 的 `OrdersPage._matches_order_tab()`，新增 `models.is_pending_purchase_order()`
- 修改文件：`weimi_manager/models.py`、`weimi_manager/ui/pages.py`、`tests/test_audit_round1.py`
- 原因：待采购判断直接包含 `status=10`，而该状态是“待付款”
- 修复：只有状态 20、付款时间大于 0、无售后、未关联采购单的订单进入待采购
- 风险或兼容说明：退款完成 200、已取消 250 均被排除；具体业务状态集合建议复审
- 新增测试：未付款、无付款时间、已付款、退款、取消、售后、已关联采购单
- 提交：`62e5364`
- 需要用户确认：是

## LL-008

- 原文件和函数：`weimi_manager/main.py` 的 `main()`
- 修改文件：`weimi_manager/main.py`、`tests/test_main_auth_gate.py`
- 原因：发行版授权配置缺失时直接打开本地工作台
- 修复：发行版 fail-closed；仅源码环境显式设置 `LANGLANG_ALLOW_UNCONFIGURED_DEMO=1` 可进入演示模式
- 风险或兼容说明：这是明确的行为变更；同时修复启动错误日志目录不存在时的异常
- 新增测试：发行版拒绝、源码无标志拒绝、源码显式演示放行、取消授权不打开业务窗口
- 提交：`3054146`
- 需要用户确认：是

## 回归与遗留

- 全量测试：`Ran 132 tests ... OK`
- Windows 冒烟：`test_window_smoke` 通过，网页店铺弹窗冒烟返回 `WEB_ONLY_DIALOG_SMOKE=OK`
- 所有测试使用临时 SQLite，没有访问真实订单，没有执行下单、支付或删除操作
- 测试结束仍会打印两个 Tk 销毁后的 `bgerror` 噪声，但退出码为 0；根因属于下一批 P0-1 的界面生命周期治理
- 本轮未开始 P0-2、P0-3、P1 或 UI 视觉重构

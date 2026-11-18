# 澜琅动销中心第 4 阶段完整开发报告

完成日期：2026-11-18

源码基线：LangLang 1.8.47

开发分支：`feature/dongxiao-center-phase4`

## 一、结论

第 4 阶段已一次性完成，不拆分 4A/4B/4C 交付。

本阶段完成了：

- 商品池到批量任务的完整创建链路。
- 持久化任务中心和多任务并发调度。
- 暂停、恢复、取消、失败重试和程序重启恢复。
- 本地安全批处理执行器与官方只读任务执行器。
- 真实订单列表、详情、金额和状态快照同步。
- 订单状态历史、订单核验任务和订单查询界面。
- 真实售后同步、售后状态与金额展示。
- 退款余额核验、人工审批、权限控制和平台执行任务。
- 官方售后同意/拒绝请求、写后状态回查和 unknown 保护。
- 执行尝试、审计日志、任务统计和异常显示。
- 正式 Tkinter 页面的任务中心、真实订单、售后工单和日志审计。
- v4 数据库迁移、checksum 冻结、失败回滚和历史兼容。
- 1000/10000 条任务创建、1000 项执行、1000 订单同步、金额规则和
  Tk 后台响应性能测试。

本阶段没有删除旧动销代码或历史表，没有修改正式部署目录，没有编译 EXE，
没有发布，没有修改 Android 客户端、Supabase migration 或旧 RPC。

## 二、完整业务流程

### 1. 商品池到任务

1. 商品目录同步和商品池管理继续复用第 3.5 阶段实现。
2. 用户在任务中心选择当前店铺的商品池。
3. `DongxiaoTaskService` 读取商品池明细并在同一店铺范围生成执行对象。
4. 任务请求计算项目摘要并写入唯一幂等键。
5. 重复请求返回原任务；相同幂等键但内容不同会被拒绝。
6. 任务明细保存商品 ID、SKU ID、数量和任务载荷。

### 2. 后台任务执行

1. `DongxiaoRuntime` 由主窗口持有，不依赖页面是否打开。
2. 调度器按全局并发上限和店铺并发上限调度任务。
3. 默认全局最多 2 个任务、每店铺最多 1 个任务。
4. 任务按等待、准备、执行、暂停、完成、部分失败、失败、取消和 unknown
   状态运行。
5. Tk 页面每 2 秒在后台读取当前任务统计，不查询网络。
6. 本地 SKU 校验任务使用批量领取、批量读取和批量写入。
7. 1000 项本地校验由优化前约 37.8 秒降低到本次实测约 0.61 秒。

### 3. 暂停、恢复、取消和重启

- 暂停：当前任务进入暂停；已完成明细保留。
- 恢复：任务回到准备状态，从剩余明细继续。
- 取消：未完成和运行中的明细标记取消；不删除任务或历史。
- 重启：本地和官方只读任务可以从 running 恢复为 preparing。
- 平台写任务：如果中断时存在运行中明细，进入 unknown，必须先核验。

### 4. 失败重试

- 网络超时、连接中断、平台系统繁忙和分钟限频映射为可重试错误。
- 参数错误、权限错误、业务状态错误等明确错误直接失败。
- 重试使用指数退避，最大尝试次数持久化，默认 3 次。
- 平台写任务默认只尝试 1 次。
- 用户可对明确失败项执行“重试失败项”。
- 每次尝试写入数据库，包括请求标识、状态、错误和时间。

### 5. 结果未知

- 平台写请求遇到断网、超时、服务端连接中断或无法确认的错误时进入 unknown。
- unknown 任务不会再被调度器自动执行。
- 用户点击“核验未知结果”后重新读取官方售后或订单状态。
- 只有官方状态变化并能确认结果时，unknown 明细才转为成功并继续任务。
- 状态未变化或无法读取时继续保持 unknown。

### 6. 真实订单

1. 使用官方 `/channels/ec/order/list/get` 分页获取订单 ID。
2. 使用 `/channels/ec/order/get` 获取订单详情。
3. 状态和金额映射到动销领域模型，金额始终为整数分。
4. 每次同步写入真实订单引用、状态快照和当前明细。
5. 重复同步不会重复生成订单，只追加新的状态快照。
6. 买家姓名、电话和地址不写入动销订单明细或普通日志。
7. 可查看订单状态历史并将选中订单创建为后台核验任务。

### 7. 售后与审批

1. 官方售后列表按更新时间或创建时间同步，范围最多 24 小时。
2. 详情保存售后单号、订单号、平台状态、类型、原因、金额和截止时间。
3. 审批金额不能超过售后申请金额。
4. 同一订单其他已完成退款、在途退款和待审批金额会从可退金额中扣除。
5. 权限 `dongxiao_aftersale_request` 控制审批申请。
6. 权限 `dongxiao_aftersale_approve` 控制审批决定。
7. 权限 `dongxiao_aftersale_execute` 控制平台售后执行任务。
8. 同一售后只允许一个待处理审批。
9. 同一售后已有执行中、unknown 或已完成执行任务时，不允许创建第二个。
10. 审批通过只代表允许执行，不会自动提交退款。
11. 平台执行前需要再次人工确认。
12. 执行后立即读取官方售后详情核验状态。

### 8. 审计与统计

- 任务创建、状态变化、尝试记录均可追溯。
- 订单同步、售后同步产生只追加审计事件。
- 审批决定产生独立审计事件。
- 审计详情经过敏感字段脱敏，数据库触发器禁止普通更新和删除。
- 总览显示商品、商品池、运行任务和 unknown 数量。
- 任务中心显示总任务、运行、暂停、完成、失败、unknown 和明细统计。
- 订单页显示当前状态和状态快照历史。
- 售后页显示平台状态、申请/审批/实付/剩余可退金额和审批记录。

## 三、功能完成矩阵

| 模块 | 本地实现 | 自动化测试 | 真实平台状态 |
|---|---|---|---|
| 商品池批量任务 | 已完成 | 已通过 | 本地能力，无需平台写接口 |
| 后台任务调度 | 已完成 | 已通过 | 本地运行 |
| 多任务并发 | 已完成 | 已通过 | 本地运行 |
| 暂停/恢复/取消 | 已完成 | 已通过 | 本地运行 |
| 重启恢复 | 已完成 | 已通过 | 本地运行 |
| 失败重试 | 已完成 | 已通过 | 本地运行 |
| unknown 核验 | 已完成 | 已通过 | 依赖官方只读接口 |
| 真实订单列表 | 已完成 | Fake 已通过 | 待真实授权店铺验收 |
| 真实订单详情 | 已完成 | Fake 已通过 | 待真实授权店铺验收 |
| 订单状态历史 | 已完成 | 已通过 | 官方数据落库 |
| 售后列表/详情 | 已完成 | Fake 已通过 | 待真实授权店铺验收 |
| 售后金额核验 | 已完成 | 已通过 | 本地纯函数/数据库 |
| 退款审批 | 已完成 | 已通过 | 本地工作流 |
| 同意售后 | 已完成 | Fake 已通过 | 待真实授权店铺验收 |
| 拒绝售后 | 已完成 | Fake 已通过 | 待真实授权店铺验收 |
| 写后回查 | 已完成 | 已通过 | 依赖官方详情接口 |
| 日志审计 | 已完成 | 已通过 | 本地 SQLite |
| Tk 正式页面 | 已完成 | 路由/控件测试通过 | 正式入口仍为 launcher.py |

Fake 仅用于自动化测试，不冒充真实订单或售后成功。

## 四、数据库迁移

v4 名称：`dongxiao_operations_v4`

主要变化：

- 扩展 `dongxiao_tasks` 调度、租约、重试和执行模式字段。
- 新增 `dongxiao_task_execution_items`。
- 新增 `dongxiao_execution_attempts`。
- 新增 `dongxiao_order_details`。
- 扩展 `dongxiao_aftersale_cases` 平台状态、类型和退款金额字段。
- 增加状态、店铺范围、唯一键和复合外键索引。

v1-v3 SQL 未修改；v4 checksum 已冻结：

`e55a03bd7d8141579f33606961242e4756b242af2aabf7f13e828873038292d5`

迁移前自动备份：

`<数据库文件名>.pre-dongxiao-v4.bak`

详细迁移与回滚说明见：

`DONGXIAO_PHASE4_DATABASE_MIGRATION_CN.md`

## 五、性能实测

测试时间：2026-11-18 13:26:05 UTC（21:26:05 中国标准时间）

测试数据库：临时隔离 SQLite，不接触真实用户数据库。

| 指标 | 结果 |
|---|---:|
| 1000 商品池明细生成批量任务 | 275.32 ms |
| 10000 商品池明细生成批量任务 | 2708.30 ms |
| 执行 1000 个本地 SKU 校验明细 | 605.84 ms |
| 10000 条任务创建 Python 峰值 | 21,770,596 B |
| 10000 条任务创建 RSS 峰值增量 | 45,875,200 B |
| 同步 1000 个真实格式订单到 SQLite | 13,376.84 ms |
| 查询 500 个订单 | 24.54 ms |
| 售后金额核验 10000 次 | 14.53 ms |
| 售后金额核验平均单次 | 1.45 微秒 |
| 500 个本地任务后台执行 | 369.86 ms |
| 后台执行期间 Tk 最大事件间隔 | 23.74 ms |
| 错误数 | 0 |

首次实现每项一次事务时，1000 项执行约 37.8 秒；改为批量领取、批量快照
读取和批量结果提交后降到 605.84 ms。以上是实际前后测量，不是估算。

## 六、全量测试

最终全量测试：

- `542 passed`
- `6 skipped`
- `1 warning`
- `18 subtests passed`
- 实际耗时：78.07 秒

跳过项：

1. `tests/test_auth.py` 中 1 个 Tk UI 测试：当前 uv Python 安装缺少
   `tk.tcl/ttk` 资源，环境无法创建 Tk。
2. `tests/test_tester_auth.py` 中 5 个安全凭据测试：测试账号安全凭据不在
   当前环境提供。

警告：

- `TesterAuthService` 被 pytest 识别为候选测试类而产生的
  `PytestCollectionWarning`；该类是既有服务类，不是测试失败。

说明：

- 动销正式页面导航测试已在这台机器上单独通过。
- `test_window_smoke` 曾因同一 Tk 资源间歇性缺失失败，单独重跑通过；
  最终全量复跑也通过。
- 跳过不影响数据库、任务引擎、订单映射、售后审批和非 Tk 集成测试。

Ruff 和 `git diff --check` 均通过。

## 七、真实接口与模拟接口

完整接口审计：

`DONGXIAO_PHASE4_INTERFACE_AUDIT_CN.md`

官方接口：

- 订单列表/详情：已实现。
- 售后列表/详情：已实现。
- 售后同意/拒绝：已实现请求契约、审批、幂等和写后回查。

模拟：

- 自动化测试使用 Fake 订单、Fake 售后和 Fake 平台响应。
- Fake 结果明确标记为测试数据，不写入生产真实订单来源。

待授权环境验证：

- 当前真实店铺是否有权限集 131。
- 真实订单翻页、金额字段和售后状态组合。
- 当前店铺能否调用售后同意/拒绝。
- 官方拒绝原因类型及凭证要求。
- 平台实际频率限制和 API 返回差异。

## 八、修改文件清单

应用与领域：

- `weimi_manager/dongxiao/application/aftersale_service.py`
- `weimi_manager/dongxiao/application/order_service.py`
- `weimi_manager/dongxiao/application/runtime.py`
- `weimi_manager/dongxiao/application/task_scheduler.py`
- `weimi_manager/dongxiao/application/task_service.py`
- `weimi_manager/dongxiao/domain/models.py`
- `weimi_manager/dongxiao/domain/operations.py`
- `weimi_manager/dongxiao/domain/rules.py`
- `weimi_manager/dongxiao/domain/state_machine.py`
- `weimi_manager/dongxiao/infrastructure/executors.py`
- `weimi_manager/dongxiao/infrastructure/migrations.py`
- `weimi_manager/dongxiao/infrastructure/platform.py`
- `weimi_manager/dongxiao/infrastructure/repository.py`
- `weimi_manager/ui/app.py`
- `weimi_manager/ui/dongxiao_page.py`

测试：

- `tests/test_dongxiao_migrations.py`
- `tests/test_dongxiao_navigation.py`
- `tests/test_dongxiao_state_machine.py`
- `tests/test_dongxiao_phase4_aftersales.py`
- `tests/test_dongxiao_phase4_orders.py`
- `tests/test_dongxiao_phase4_tasks.py`

工具与文档：

- `tools/benchmark_dongxiao_phase4.py`
- `tools/make_phase4_delivery_bundle.py`
- `DONGXIAO_PHASE4_INTERFACE_AUDIT_CN.md`
- `DONGXIAO_PHASE4_DATABASE_MIGRATION_CN.md`
- `DONGXIAO_PHASE4_PERFORMANCE_20261118.json`
- `DONGXIAO_PHASE4_REPORT_CN.md`
- `FEATURE_REGRESSION_CHECKLIST_CN.md`

## 九、未完成/外部阻断

- 未在真实授权店铺执行订单和售后接口，不宣称“真实平台已验证”。
- 权限集 131 和实际退款权限需正式店铺授权后核验。
- 未通过官方接口执行真实退款测试，避免影响真实买家和资金。
- 未开发隐藏接口、Frida 注入、ADB/OCR 绕过或虚假订单能力。
- 商品池任务当前实际执行类型是本地 SKU/库存/价格安全校验；真实购物车、
  下单和支付不属于本阶段已授权能力，也没有官方公开接口依据。
- 平台写能力只实现官方售后同意/拒绝，不扩展到虚假交易。

## 十、部署状态

- 未编译 EXE。
- 未部署。
- 未发布。
- 未修改 `D:\WG\LangLang`。
- 未删除旧动销模块和历史数据。
- 未修改 Android、Supabase migration 或旧 RPC。

完成后停止，等待对完整源码进行独立审查，不进入后续阶段。

# 澜琅 1.6.51 上货审计第一批修复报告

## 基线

- 源码基线标签：`upload-audit-baseline-1.6.51`
- 修复分支：`fix/upload-audit-round1`
- 当前版本：`1.6.51`，未修改版本号，未发布
- 数据库备份目录：`D:\GPT\upload_audit_db_backup_20261108_231500`
- 数据库备份清单：`D:\GPT\upload_audit_db_backup_20261108_231500\BACKUP_MANIFEST.txt`
- 三个真实数据库在修改前均通过 SQLite `integrity_check=ok`
- 本批没有读取或修改真实店铺、商品、订单记录；自动化测试全部使用模拟对象和临时 SQLite

## UL-P0-01 切页不能覆盖运行任务

- 原问题：`UploadPage.refresh()` 整体替换 `_tasks`，后台任务仍持有旧对象；`_save_tasks()` 随后保存新列表，已成功结果丢失
- 修改文件：`weimi_manager/ui/upload_page.py`
- 关键函数：`UploadPage.__init__()`、`refresh()`、`_save_tasks()`、`_tasks_snapshot_json()`、`_task_list_snapshot()`、`_render_tasks()`、`_start_tasks()`
- 修复：
  - 使用 `_task_lock` 串行保护任务快照和关键状态修改
  - 运行中或调度请求未完成时，`refresh()` 不替换任务真相源
  - 任务和 item 增加稳定 `id` / `item_id`
  - worker 结果通过 task ID 和 item ID 定位到当前任务对象
  - UI 使用任务列表快照渲染，避免与后台状态更新直接竞争
- 新测试：
  - 运行中切页 30 次，成功 item 的 `product_id` 保持 `P_TEST_001`
  - 旧任务缺少 item ID 时自动补齐并持久化
- 功能保留：页面切换、任务列表、暂停、重试、筛选、统计逻辑均保留
- 提交：`95f5bc0`、`4766bd7`、`a5e1f5f`

## UL-P0-02 调度器拒绝时恢复状态

- 原问题：`_start_tasks()` 在 `window.run_task()` 返回前设置 `_running=True`，调度器拒绝后页面永久显示运行中
- 修改文件：`weimi_manager/ui/upload_page.py`
- 关键函数：`_start_tasks()`、`_update_task_actions()`
- 修复：
  - 使用 `_run_requested` 表示调度请求阶段
  - worker 真正开始后才设置 `_running=True`
  - `run_task()` 返回 `False` 时恢复 `_running`、`_run_requested` 和 `_paused`
  - 任务状态保持 `pending`，不会误改 item 状态
  - 运行期间开始、重试、清空和删除按钮禁用
- 新测试：模拟调度器返回 `False`，验证任务未启动、没有保存运行状态、没有被标记为运行中
- 功能保留：全局任务互斥和原调度器行为未改
- 提交：`9238099`、`a5e1f5f`

## UL-P0-03 缺失来源商品 ID

- 原问题：同一文件多条无 ID 商品全部回退为文件 stem，后续商品被默认去重
- 修改文件：`weimi_manager/ui/upload_page.py`
- 关键函数：新增 `_stable_source_fingerprint()`，修改 `_parse_package()`
- 修复：
  - 显式 `source_product_id/product_id/productId/uniqueId/id` 继续原样使用
  - 缺失 ID 时根据平台、标题、价格、库存、规格、SKU、图片和完整来源数据生成 SHA256 指纹
  - 同一文件完全相同的记录使用稳定出现序号 `-2`、`-3`
  - 跨文件相同商品生成相同指纹，仍可被原去重策略识别
  - 不再使用文件名或随机 ID 作为统一回退
- 新测试：
  - 同文件两个无 ID 商品得到不同且可复现的 ID
  - 完全重复记录按出现顺序稳定区分
  - 跨文件相同商品指纹一致
- 功能保留：显式来源 ID、本店铺/本电脑/关闭去重三种策略均未改变
- 提交：`8f326ac`

## UL-P0-04 暂停、关闭、落盘和未知结果

- 原问题：任务每 50 条才整体保存；daemon 线程在远端成功但本地保存前退出时，恢复逻辑可能误判为未提交
- 修改文件：
  - `weimi_manager/ui/upload_page.py`
  - `weimi_manager/ui/app.py`
  - `weimi_manager/db.py`
- 关键函数：
  - 新增 `_recover_upload_tasks()`
  - 新增 `UploadPage.prepare_for_close()`、`_commit_upload_success()`
  - 新增 `Database.commit_upload_task_result()`
  - 修改 `MainWindow._on_close()`
  - 修改 `_start_tasks()` 的提交、结果和结束状态处理
- 修复：
  - 每次远端提交前持久化 item 的 `submitting` 状态、attempt ID 和开始时间
  - 远端成功后将任务快照和 `upload_history` 在同一 SQLite 事务中提交
  - 网络中断、超时、线程异常或响应缺少商品 ID 时标记 `verification_pending`
  - 本地保存失败时保留远端商品 ID，但标记待核实，不进入自动重试
  - 重启时将 `submitting/running` 恢复为 `verification_pending`，将未开始的 `queued` 恢复为 `paused`
  - 待核实商品不会被再次自动提交
  - 关闭主窗口前调用 `prepare_for_close()` 持久化恢复状态
- 新测试：
  - 中断任务恢复与稳定 item ID
  - 远端成功但本地事务失败
  - 多店并发结果隔离
  - 等待核实的商品二次运行不会再次调用创建 API
  - 任务快照和上传历史原子提交
  - 主窗口关闭前执行上货恢复标记
- 数据库迁移：无 Schema 变更，只使用现有 `app_settings` 和 `upload_history`
- 功能保留：原 12 并发默认值、轮转队列、限流、错误提示和既有 API 均未改变
- 提交：`669f479`、`a58bba7`

## UL-P0-05 运行期间清空、删除和暂停

- 原问题：清空任务只设置暂停并替换 `_tasks`，在途 worker 仍持有旧对象，界面却提示已清空
- 修改文件：`weimi_manager/ui/upload_page.py`
- 关键函数：`_clear_all_tasks()`、`_clear_completed()`、`_delete_tasks()`、`_retry_failed()`、`_update_task_actions()`
- 修复：
  - 运行中或调度请求未完成时禁止清空全部、清理完成项、删除任务和重试
  - 提示用户先暂停并等待在途商品结束
  - 暂停文案保持“当前商品处理结束后停止”，不宣称已经停止
  - 运行结束后恢复操作能力
- 新测试：运行中清空/删除不改变任务；暂停文案包含在途等待语义
- 功能保留：任务结束后的原清空、完成项清理和删除功能保持不变
- 提交：`13cc079`

## 文件修改清单

- `weimi_manager/ui/upload_page.py`
  - 稳定来源指纹
  - task/item 稳定 ID
  - 任务锁与快照
  - 切页保护
  - 调度拒绝恢复
  - 提交中、待核实和恢复状态
  - 运行中危险操作保护
- `weimi_manager/db.py`
  - `commit_upload_task_result()`：任务快照与上传历史单事务提交
- `weimi_manager/ui/app.py`
  - `_on_close()`：关闭前触发上货恢复状态持久化
- `tests/test_upload_page.py`
  - 新增切页、调度拒绝、无 ID、网络未知、落盘失败、关闭、暂停、清空、多店并发测试
- `tests/test_core.py`
  - 新增主窗口关闭前的上货恢复测试

## 回归结果

- 单进程全量命令退出码：`0`
- 分模块实测：146/146 通过
- `test_upload_page.py`：30/30 通过
- `test_core.py`：45/45 通过
- 其余 8 个模块：71/71 通过
- Windows 主窗口冒烟：`test_window_smoke` 通过，约 4.8 秒
- 测试日志：`D:\GPT\澜琅_1.6.51_上货审计第一批_测试日志.txt`
- 测试结束仍有既有 Tk 销毁回调 `bgerror` 噪声；退出码为 0，该日志队列问题属于后续 P0-1 性能治理，不在本批范围

## 回滚

1. 停止澜琅进程。
2. 切回基线标签：`git switch --detach upload-audit-baseline-1.6.51`。
3. 如果代码回滚后仍需恢复数据库，先关闭所有数据库连接。
4. 从 `D:\GPT\upload_audit_db_backup_20261108_231500` 选择对应备份。
5. 先保留当前数据库副本，再将备份复制回原数据库路径。
6. 本批没有数据库结构迁移，因此不需要降级表结构。

## 本批未做

- 未修改并发默认值 12/32
- 未关闭或改变接口限流
- 未取消已有上货 API
- 未进行数据包后台导入、图片缓存、任务表重构、UI 重做或下一批性能优化
- 未使用真实微信店铺验证远端创建、限流和真实 Windows 发布包

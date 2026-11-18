# 澜琅 重构1.8.2 修改清单

## 云端授权与同步

- `weimi_manager/cloud_commands.py`
  - 统一服务、浏览器和数据库命令授权。
  - 批量授权失败时关闭已授权的请求。
  - 校验云端返回的命令和店铺与客户端请求一致。
  - 店铺写入使用串行云端事务锁、确认和本地回滚。
  - 共享设置与订单工作台云端写入。
- `weimi_manager/account_store_sync.py`
  - 增加无二次拉取的 `push_current`。
  - 个人账户上传结果统一返回已保存状态。
  - 组织工作台和共享设置拉取。
- `weimi_manager/auth_client.py`
  - 增加工作台和组织设置 RPC 客户端。
- `weimi_manager/db.py`
  - 增加设置删除、远端设置导入、工作台远端导入和店铺快照回滚。

## 店铺管理与响应性

- `weimi_manager/ui/app.py`
  - Tk 数据库、服务和浏览器会话套用统一授权适配器。
  - 账户同步使用原始数据库，避免授权递归。
  - 数据库重载后页面保留授权适配器。
  - 图标读取失败不再导致主窗口启动崩溃。
- `weimi_manager/ui/store_page.py`
  - 店铺新增、编辑、删除和批量导入移入后台任务。
- `weimi_manager/ui/business_tools.py`
  - 分组创建、改名、删除和批量归组移入后台任务。
- `weimi_manager/pyside/workspace.py`
  - PySide6 工作区套用同一授权层。
- `weimi_manager/pyside/pages/stores.py`
  - PySide 店铺增删改和批量导入使用后台任务。
- `weimi_manager/pyside/pages/store_groups.py`
  - PySide 分组操作使用后台任务。

## 服务端

- `supabase/migrations/202611120005_store_organization_permissions.sql`
  - 新增 `stores.organize`。
  - 自动化命令改为非单店 Scoped。
  - 子账号删除店铺按删除权限和店铺授权校验。

## 测试与审计

- 新增命令授权、店铺云事务回滚、双数据库同步、删除和设置同步测试。
- `tools/audit_cloud_commands.py` 识别服务、浏览器、数据库和本机状态入口。
- `BUTTON_NETWORK_DEPENDENCY_AUDIT.md` 生成 335 行完整清单。

## 版本与文档

- 版本更新为 `1.8.2`。
- 更新 README、交接记录、发行说明、测试报告和迁移报告。
- `update_manifest.json` 保持空下载地址，仅作分享版清单。

# 澜琅 1.7.0 重构交接

日期：2026-11-10

## 交付结论

1.7.0 已从 1.6.63 业务主线分出独立 Git 分支，采用 PySide6
主窗口、缓存页面和统一后台任务框架，保留 1.6.x 的数据库、账户目录、
DPAPI、认证同步、微信官方接口、网页会话和旧业务服务。

本次产物是“可运行、可打包、可自动更新、自动化回归通过的 1.7.0
框架迁移版”。它不是“所有真实平台写操作都已完成生产验收”的最终声明。
平台接口和真实数据场景仍须使用隔离测试店铺逐项验收。

## 基线

| 项目 | 值 |
| --- | --- |
| 旧版稳定分支 | `stable/1.6.63` |
| 当前重构分支 | `release/1.7.0-pyside` |
| 旧版基线提交 | `18152e2 chore: freeze langlang 1.6.63 business baseline` |
| 1.6.62 归档 SHA256 | `5a314ba225ef8c32cf157c8d05bbf07c87968f5fecaef676a72edb6392709565` |
| 2.0.1 用途 | PySide6 架构、控件、主题、任务管理和打包参考 |
| 业务真源 | 1.6.63 工作区中的接口、数据库和服务实现 |

旧版源码没有被 1.7.0 工作区就地覆盖。1.6.63 稳定线和旧 Tk 入口均保留。

## 架构边界

```text
PySide6 页面
    ↓
Workspace / 统一 TaskManager
    ↓
1.6.63 已验证业务服务
    ↓
旧 SQLite、微信官方接口、私有后台接口、Edge 网页会话
```

- 页面按需创建，并保存在 `QStackedWidget` 中，切换页面后不丢状态。
- `TaskManager` 使用 8 个线程，可同时运行不同业务任务。
- 同一业务 key 的运行中任务会拒绝重复提交；不同店铺或不同页面不再被全局
  “进程已在进行中”状态互相阻塞。
- 网络、Playwright 和耗时数据库操作不得在 Qt 主线程执行。
- 旧 `SyncService`、`Database`、`WechatShopClient`、`ShopPrivateClient`、
  `WechatWebSession`、采购和上货引擎继续作为业务实现，避免用 2.0.1
  的缩减版服务替换旧能力。
- 新页面不下发模拟平台数据；未完成或未验证的功能在文档和界面中明确标记。

## 已实现

- PySide6 登录、注册、找回密码、授权码和账户切换。
- 24 个旧业务路由均有专用 Qt 页面，不落到通用占位页。
- 工作台、诊断、店铺、订单工作台、订单提取、已解析订单、商品、上传、
  运营售后、客服、营销、经营数据、店铺主页、资金、采购、自动化、设置、
  账号、店铺分组、活动、违规库、动销、无流量、托管和大屏。
- 主题、窗口尺寸、页面缓存、状态栏任务计数和错误提示持久化。
- 账户目录隔离、DPAPI、认证会话、设备授权和 AccountStoreSync。
- 旧版为 `_internal/assets/auth_config.json` 的客户端连接配置恢复。
- 更新安装时保留旧版客户端配置、`.env.local` 和回滚目录。
- 固定 Python 3.11.9、隔离 PATH、资源安全检查、发行 ZIP 和 SHA256。

## 构建和发行

最终构建目录：

```text
D:\GPT\builds\1.7.0\dist\LangLang
```

固定构建入口：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File D:\GPT\langlang-1.7.0-pyside\tools\build_pyside.ps1 `
  -Python D:\2.0微店\.python311\python.exe `
  -OutputRoot D:\GPT\builds\1.7.0
```

发行构建可通过 `-ClientConfig` 把现有 1.6.x 的客户端连接配置放入
`_internal/assets/auth_config.json`。该配置不提交到源码仓库，不写入日志，
也不在审计输出中展示内容。

发行打包入口：

```powershell
D:\2.0微店\.python311\python.exe `
  D:\GPT\langlang-1.7.0-pyside\tools\package_pyside.py `
  --program D:\GPT\builds\1.7.0\dist\LangLang `
  --output D:\GPT\langlang-1.7.0-pyside\release
```

自动更新遵循：

```text
GitHub Releases latest/download/update_manifest.json
    ↓
版本比较
    ↓
下载与 SHA256 校验
    ↓
ZIP 路径安全检查
    ↓
目录预复制、进程等待、目录交换
    ↓
新版启动失败时恢复旧目录
```

## 回退

源码回退：

```powershell
git -C D:\GPT\langlang-1.7.0-pyside switch stable/1.6.63
```

旧界面回退：运行工作区中的 `run_legacy_tk.bat`。

程序回退：自动更新会保留 `LangLang.update-backup-*`。若新版异常，
关闭澜琅后把备份目录改回原程序目录；固定用户数据目录
`%LOCALAPPDATA%\LangLang\data` 不应删除或覆盖。

## 未完成的生产验收

- 未连接任何生产店铺，未执行真实上架、采购、付款、发货、删除或迁移。
- 未进行真实多店扫码、主/子账号、Cookie 导入退出和平台会话失效恢复。
- 未执行真实商品七页签全量快照、复杂商品表单、图片、类目、库存和批量写操作。
- 未执行真实订单解密、改价、改址、物流、售后、采购、活动、优惠券和资金操作。
- 未执行真实数据包上传、类目审核、远端成功/本地失败、暂停恢复和未知结果核实。
- 未用真实 1.6.x 用户库完成升级演练；自动化只覆盖合成数据库和迁移逻辑。
- 未完成 Windows 10 干净机、8 小时稳定性、弱网和平台风控测试。
- 静态 PE 检查仍对系统 `COMCTL32.dll` 报导出序号告警，未计为通过项；
  本次发行 ZIP 已实际启动并进入登录窗口。

逐模块状态见 [FUNCTIONAL_PARITY_1.7.0.md](FUNCTIONAL_PARITY_1.7.0.md)，
测试证据见 [TEST_REPORT_1.7.0.md](TEST_REPORT_1.7.0.md)。

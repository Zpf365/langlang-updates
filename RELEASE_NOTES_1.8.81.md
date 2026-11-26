# 澜琅 1.8.81

## 更新提示改为左上角徽标

- 登录页和登录后的 WebUI 都不再为普通可选更新自动弹窗。
- 自动检查发现新版本时，只在左上角显示可点击的紧凑徽标。
- 徽标显示“发现新版本 vX”和“当前 vY”，跨路由持续保留。
- 无更新或自动检查失败时不显示徽标，也不打断当前操作。
- 点击徽标才打开更新说明和“立即更新”确认。
- 确认后继续使用整页锁定、不可关闭、真实下载/校验/解压、自动安装重启流程。
- 登录页保持启动检查并每 30 分钟静默刷新；WebUI 同样每 30 分钟静默刷新。
- 启动前普通可选更新不再弹窗，交给应用内徽标；mandatory 更新仍可阻断处理。

## 运行日志与导出

- 默认日志根目录为软件根目录 `logs/YYYY-MM-DD/`；根目录不可写时回退到
  `%LOCALAPPDATA%\\LangLang\\logs\\YYYY-MM-DD\\`。
- 每次运行生成独立 `run_id`，保存会话、环境、账户、数据目录、启动和退出信息。
- 每日生成 `app.log`、`webui-events.jsonl`、`network.jsonl`、
  `errors.log` 和 `session-<run_id>.json`。
- WebUI 全局记录页面导航和实际点击，不去监听 mousemove/scroll 等高频事件。
- controller 统一记录 RPC action、店铺、耗时、结果、错误码以及手动/自动刷新来源。
- 密码、密钥、Cookie、Token、Authorization、Session、手机号、地址和邮箱统一脱敏。
- 日志按 20MB 分片，默认保留 30 天，同类网络错误 10 秒内聚合抑制。
- “更多”页显示实际日志路径、当前 run_id、最近错误数，并支持导出最近 1 天、
  最近 7 天日志和打开日志目录。
- 导出 ZIP 仅包含日志，不包含数据库、密钥、Cookie 或上传数据包。
- 更新器原地覆盖程序时会保留根目录 `logs`。

## 边界

- 正式入口继续只启动 WebUI，不恢复旧 Tk 工作台。
- 本机 `D:\\WG\\LangLang` 不部署、不关闭、不重启、不覆盖。

## 最终候选验证

- “更多”菜单新增可直接到达的“运行日志”入口，不再需要借道其他模块。
- 每日日志初始化时即创建 `app.log`、`webui-events.jsonl`、`network.jsonl`
  和 `errors.log`；无异常时保留空的 `errors.log`，导出文件集完整。
- 全量 pytest：`694 passed, 6 skipped, 21 subtests passed`；Vitest：
  `89 passed`；Vite production build 与正式 `LangLang.spec` PyInstaller
  构建通过。
- 最终 EXE 已真实打开 WebUI：左上角更新徽标跨“店铺管理 → 订单管理 →
  运行日志”持续显示；点击“导出最近 1 天”实际生成 ZIP。
- 导出 ZIP 仅包含 `logs/2026-11-22/` 下的日志文件，不包含数据库、密钥、
  Cookie 或上传数据包；日志中的导航、点击、真实 RPC、耗时和结果均可核对。
- 更新器回归测试确认原安装目录的 `logs` 在失败回滚后仍完整保留。
- 当前候选仅供本线程验收，未发布 GitHub，未部署或触碰用户本机
  `D:\\WG\\LangLang`。

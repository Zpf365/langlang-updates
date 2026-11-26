# 澜琅 1.8.79

## 店铺成交统计修复

- 今日销售、今日订单、成交人数改为微信后台 `home-overview-shop-realtime` 的
  `pay_gmv / pay_order_cnt / pay_uv`，不再用本地订单缓存冒充实时成交。
- 昨日、近7天、近30天改用 `home-overview-shop-offline`，按完整自然日区间请求；
  近7天和近30天结束于昨天，避免把今天实时值串入所有周期。
- 本地订单求和只作为网页统计接口完全不可用时的降级来源。
- 每个周期独立保存更新状态、数据来源、更新时间和错误；接口失败保留最后成功值并标记旧值。
- WebUI 销售金额 tooltip 显示“微信小店实时成交统计 / 周期成交统计”、更新时间、
  失败原因和旧值状态。
- 商品退款金额继续单独读取后台 `pay_refund_gmv`，成交金额采用后台成交口径。

## 自动刷新计时

- 修复切换页面后自动刷新重新等待 10 分钟的问题。
- 页面进入 KeepAlive 非激活状态时不再取消或重置 timer。
- 浏览器窗口隐藏时暂停实际 timer，但保留绝对 `nextRunAt`；恢复时按剩余时间继续，
  已到期只立即补跑一次，然后进入下一周期。
- 手动刷新仍是唯一会将下一次刷新重置为 10 分钟后的场景。
- 不同页面/账户不共享运行状态；页面销毁、退出或切换账户时清理 timer。

## 真实只读联调

店铺 18「言参冠旺商行」只读结果：

- 今日：`20286 分 / 11 单 / 6 人`
- 昨日：`0 分 / 0 单 / 0 人`
- 近7天：`379321 分 / 80 单 / 6 人`
- 近30天：`1565005 分 / 302 单 / 8 人`

未执行任何平台写操作。

## 验证

- 新增后台成交覆盖本地缓存、后续刷新更新、四周期独立、失败旧值、零值/缺失、
  两店隔离和自动刷新绝对截止时间测试。
- 全量 pytest 最终复跑：`672 passed, 6 skipped, 21 subtests passed`；
  额外跳过项是本机 uv Python 缺少 `tk.tcl` 所需 `scale.tcl` 的环境问题，
  其余跳过项均为未配置安全测试凭据。
- Vitest：`85 passed`；Vite production build 通过。
- 正式 `LangLang.spec` / PyInstaller 构建通过。

## 边界

- 正式 EXE 继续只启动 WebUI，不恢复旧 Tk 工作台。
- 本机 `D:\WG\LangLang` 不部署、不关闭、不重启、不覆盖。

## 候选验收证据

- 候选 EXE：
  `release\LangLang_1.8.79_Windows_x64\LangLang.exe`
  SHA256：`CA555DC01FD2E33045E82B8E51AFDE432527C73BF71DB3C372BE2BA3D73F9EB0`。
- Windows 包：
  `release\LangLang-1.8.79-win64.zip`
  SHA256：`0EA7BA3859AF67C38A3731CE2B971A66204213F7CE94211F03C32D6D5F332A63`。
- 包内 `_internal\webui\dist\index.html`、全部前端 assets、
  `Qt6WebEngineCore.dll`、`Qt6WebEngineWidgets.dll` 均存在；
  PYZ 中存在 `weimi_manager.webui.main`，不存在
  `weimi_manager.main` 或 `weimi_manager.ui.app`。
- 最终候选 EXE 使用 `--smoke-data` 实际启动，窗口为 WebUI 店铺管理，
  显示 `v1.8.79`、自动刷新倒计时和四周期销售列。截图：
  `deliveries\1.8.79\final-exe-webui-sales.png`。
- 在最终候选 EXE 内从店铺管理切到订单管理再返回，店铺自动刷新倒计时由
  `10:01` 连续减到 `9:43`，没有重新等待满 10 分钟。
- 最终候选 EXE 无参数实际启动，窗口标题为
  “澜琅 · 登录 重构1.8.79”，进入 WebUI 登录页，未进入旧 Tk。
  截图：`deliveries\1.8.79\final-exe-webui-login.png`。
- 检查时本机 `D:\WG\LangLang\LangLang.exe` SHA256 为
  `94F1C54C569D26F4FBD126DE90E18165CA60B80EC558FB27AEC4391F15FA4266`
  （对应已验收的 1.8.78 文件），本次未部署、覆盖、关闭或重启该目录，
  也未执行 GitHub 发布。

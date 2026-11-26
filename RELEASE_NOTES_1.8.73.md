# 澜琅 1.8.73

## 店铺管理页修复

- 主表移除信誉分、经营类目、经营分三列，不显示表头、空占位或伪值；底层字段和历史数据保留。
- 保证金列继续保留，只采用微信小店 `getDepositMPayState` 返回的 `depositBalance`，不把类目应缴保证金或应补金额冒充当前保证金。
- 保证金读取同时兼容顶层、`data` 嵌套、数字和字符串；缺失或失败时显示“暂无真实数据/本次未更新”，并保留上次成功值、更新时间和错误。
- 体验分、商品体验、物流体验、服务体验四项分别持久化最后成功值、变化与刷新状态。
- 局部字段缺失不会覆盖旧值或三角；连续列表轮询保持三角稳定；指标恢复后更新变化；新成功值不变时清除旧变化。
- 三角显示带正负号、旧值→新值和更新时间 tooltip，保持现有红涨绿跌样式。
- AppID 默认只显示前 4 位加省略号，按行 show/hide，展开 60 秒后自动折叠；不读取或请求额外网络数据。
- 新增“商品曝光”三列分组：今日、昨日、7日；缺失显示“暂无真实数据”，真实 0 保留为 0。
- 新增“在线商品数”：优先官方商品列表 `total_num(status=5)`，无可用 AppSecret 时回退商家后台商品“销售中”页签真实总数。
- 新增“今日上新”：只统计 `product_catalog.first_seen_at` 落在上海自然日的不同商品；今日未完成目录扫描显示“暂无观测”，已完成且无新增才显示 0。
- 金额/数量右对齐并使用千分位；表格保持横向滚动，不改卡片布局。

### 最终主表列顺序

勾选、序号、店铺名称、Appid、Appsecret、商品曝光（今日/昨日/7日）、
在线商品数、今日上新、登录状态、今日销售、昨日销售、7日销售、30日销售、
可提现余额、待结算余额、体验分、商品分、物流分、服务分、保证金、操作。

## 真实来源

- 四项体验分：微信小店私有统计接口 `SCORE_STATS_PATH`，字段 `dsrScore`、`productScore`、`deliverScore`、`serviceScore`。
- 保证金：微信小店私有接口 `getDepositMPayState` 的 `depositBalance`，单位为分。
- 今日/昨日/7日商品曝光：`getShopRecommendProductsStat` 的 `total.product_view_uv`；7日使用真实 `dateFormat=2`、`dateType=0`、`dateRange=7` 区间，不使用推算。
- 在线商品数：微信开放平台商品列表 `total_num` 且 `status=5`；网页回退为微信小店商家后台商品扫描 `productStatus=[5]` 的 `totalNum`。
- 今日上新：本地 `product_catalog.first_seen_at` 在 `Asia/Shanghai` 当日的不同商品数，并由当日成功的商品目录同步记录确认扫描已完成。
- 已只读探测当前登录店铺：真实响应顶层含字符串 `depositBalance`，本次读取为 100000 分。

## 验证

- 默认全量 pytest：`611 passed, 7 skipped, 18 subtests passed`。
- 默认 pytest 不收集 `tests/isolated_dongxiao`，不会触发已知 Windows
  `0x80000003` 动销并发崩溃。
- Vitest 全量：`56 passed`。
- Vite production build：通过。
- PyInstaller WebUI 候选包：通过。
- 隔离 `--smoke-data`：候选 EXE 运行 12 秒保持存活，随后仅终止候选进程。
- 候选 PYZ 递归检查与 `dist` 文件检查均未发现动销运行时模块。

## 动销非破坏隔离

- Tk 主窗口、PySide 主窗口和 WebUI 不再注册“动销中心/动销相关”入口。
- 默认应用不导入、初始化、启动、调度或关闭动销 runtime。
- 数据库默认初始化不再静态导入或自动执行动销、旧 sales-related 迁移；
  设置 `LANGLANG_ENABLE_DONGXIAO_ISOLATED=1` 时才延迟执行原迁移。
- 动销运行时、页面、买手流程和旧 sales-related 测试均保留在独立源码备份
  `isolated/dongxiao_backup_1_8_73/`。
- 34 个相关测试保留在 `tests/isolated_dongxiao/`，默认全量测试不运行，
  可按隔离说明单独执行。
- 动销表、历史记录、账号、任务和日志均未删除或改写。

## 边界

- 未部署、关闭、重启或覆盖本机 `D:\WG\LangLang` 1.8.70。
- 未发布 GitHub Release，未更新远程 Latest。
- 真实店铺保证金探测只执行只读接口；未执行任何换单、付款或店铺写操作。

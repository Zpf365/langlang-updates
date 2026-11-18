# 澜琅动销中心第 3.5 阶段验收报告

完成日期：2026-11-18

基线版本：LangLang 1.8.47

开发分支：`feature/dongxiao-center-phase3-5`

基线提交：`ee1cf1f test: cover Dongxiao recovery and pool lifecycle`

源码目录：`D:\11.18新项目微信\微信群控\1.8.47\项目源码`

正式部署目录：`D:\WG\LangLang`

本阶段未修改正式部署目录，未编译或发布 EXE，未修改 Android 客户端、
Supabase migration、旧 RPC、旧 `sales_*` 表或旧动销模块。

## 一、阶段结论

第 3.5 阶段已完成商品目录专项审查和补强。审查没有只依据类名认定官方接口，
而是沿源码逐层追踪到真实 HTTP 路径，并与微信开放文档逐项核对。

本阶段修复的主要真实使用风险：

1. 官方商品详情漏用了有文档依据的 `total_sold_num`。
2. SKU 可售状态原先未检查 `skus[].status`。
3. 原实现读取未在官方文档出现的 SKU 起购和默认规格字段，存在猜测风险。
4. 完整详情未返回的旧 SKU 原先会残留为当前可售数据。
5. 分页第二页失败时没有结构化“部分同步”结果。
6. 商品不再返回时缺少与网络失败隔离的目录缺失标记。
7. `40001/42001` 令牌失效没有自动刷新重试。
8. 真实网络异常类型与旧适配器假客户端模拟的类型不一致。
9. 商品池 CSV 缺少 Excel 公式注入防护和可逆重新导入。
10. 缓存没有明确区分实时、非实时和可能失效状态。
11. 微信小店分享文本缺少人工关联路径。

## 二、业务目录

本阶段没有重建第 3 阶段目录，只在既有独立业务边界内补强：

```text
weimi_manager/dongxiao/
  application/
    catalog_service.py
    import_service.py
    pool_service.py
  domain/
    catalog.py
    enums.py
    models.py
    queries.py
    rules.py
    state_machine.py
  infrastructure/
    catalog.py
    migrations.py
    repository.py
  navigation.py
```

正式 Tkinter 页面仍为 `weimi_manager/ui/dongxiao_page.py`，继承现有
`BasePage`，继续使用 `sales_related` 路由键和已有权限控制。

## 三、真实接口审计摘要

完整逐接口证据见：

`DONGXIAO_PHASE3_5_INTERFACE_AUDIT_CN.md`

已确认的官方路径：

| 能力 | 路径 | 状态 |
|---|---|---|
| 稳定版令牌 | `POST /cgi-bin/stable_token` | 官方文档已确认 |
| 商品列表 | `POST /channels/ec/product/list/get` | 官方文档已确认 |
| 商品详情 | `POST /channels/ec/product/get` | 官方文档已确认 |
| 商品 H5 短链 | `POST /channels/ec/product/h5url/get` | 官方文档已确认，本阶段未调用 |
| 商品微信口令 | `POST /channels/ec/product/taglink/get` | 官方文档已确认为生成接口 |

认证方式为 AppID + AppSecret 获取 `access_token`，官方业务请求通过查询参数
传递 `access_token`。当前未接入服务商 `authorizer_access_token`，也没有
自建请求签名。

分页使用官方 `next_key` 原样回传，`page_size` 现在由适配器真实传入并限制为
1 到 30。客户端和服务层均防止游标重复导致无限循环。

官方商品详情没有可信的线上首次上架时间字段，所以 `listing_time` 保持
`None`。销量使用文档中的 `total_sold_num`，来源标记为官方 API。

官方商品详情没有 SKU 起购数量或默认 SKU 字段。内部 `purchase_min=1` 只表示
模型下限，同时记录 `purchase_min_verified=false`。SKU 引擎会明确返回
“接口未提供可信起购数量，无法确定”。商品级 `limited_info.limited_buy_num`
按文档映射为防止重复购买的数量上限。

## 四、同步数据一致性

### 当前状态与历史状态

v3 新增商品/SKU `presence_state` 和 `missing_since`：

- `present`：完整详情最近一次返回了该商品或 SKU。
- `missing`：一次完整目录同步确认未再返回，但记录和历史快照保留。
- `state` 继续表示最后一次可信的平台上下架状态，不因目录缺失被清空。
- `dongxiao_product_snapshots` 不做删除或覆盖。

### 网络失败边界

只有完整目录分页无错误、无取消、所有单商品详情均成功时，才执行目录级缺失
清理。任一页失败、任一详情失败或用户取消时，不改变未成功商品的 presence。

第一页成功、第二页失败时会保存第一页，返回 `status=partial`，并保留第二次
请求未返回商品的旧数据。

### 专项场景结果

| 场景 | 结果 |
|---|---|
| 10 个 SKU 同步后只有 8 个 | 2 个旧 SKU 标记 missing，当前列表为 8，历史仍可查 |
| 商品价格变化 | 当前价格更新，历史快照保留 |
| 库存变 0 | 当前库存/可售状态更新，SKU 不删除 |
| 商品下架 | 当前状态改为 inactive，历史保留 |
| 商品不再返回且完整同步成功 | 商品和其 SKU 标记 missing，记录不删除 |
| 单商品详情部分失败 | 该商品保持旧值，结果非 complete |
| 第一页成功、第二页失败 | 第一页保存；旧商品不清理；结果 partial |
| 同一商品连续同步三次 | 商品/SKU 不重复；三组历史快照保留 |
| 两个入口并发导入同一商品 | 数据库唯一键和事务保证最终只有一份当前记录 |
| 同步过程中切换店铺 | 清理始终限定 `store_id + store_app_id`，不跨店 |

## 五、SKU 规则补强

- 任意可售 SKU：按价格、SKU ID 确定性排序。
- 最低价 SKU：价格升序，同价按 SKU ID 升序。
- 最高价 SKU：价格降序，同价按 SKU ID 升序。
- 指定 SKU：只接受当前在售且通过库存/价格/限购检查的指定 ID。
- 默认 SKU：只使用已验证的默认标识；官方目录未提供时返回
  `DEFAULT_MISSING`，不自行猜测。
- 商品下架、价格变化、缺货、库存不足、起购、限购和未知起购均有明确失败
  原因。
- 规则层仍为纯函数，不访问网络或数据库。

## 六、分享文本与商品链接

官方文档只确认“根据商品 ID 生成商品口令/H5 链接”，未发现反向解析任意
分享文本的公开接口。因此当前不会从口令码猜商品 ID 或店铺身份。

本阶段新增人工关联：

1. 原始 `#微信小店://` 文本继续保存为“待解析”。
2. 用户在导入预览中选择该行。
3. 输入当前店铺授权商品目录中的真实商品 ID。
4. 后端验证商品存在且属于当前店铺。
5. 该行改为可导入，原始分享文本继续保留。
6. 用户保存后可按正常流程加入当前店铺商品池。

无法实现：任意第三方分享文本的全自动商品 ID/店铺身份解析。需要微信官方
提供明确的解析 API、权限范围、返回字段和错误码后重新评估。

## 七、CSV 安全与往返

- 导出字段包含商品 ID、SKU ID、排序、标题、店铺名、状态、价格分、库存和
  presence。
- 以 `=`、`+`、`-`、`@`、制表符或回车开头的文本会前置单引号，防止 Excel
  公式执行。
- 安全前缀是可逆的；对本身以单引号和公式字符开头的文本也有额外保护。
- 导出使用 UTF-8 with BOM 写入，中文在 Excel 中正常显示。
- 新增商品池 CSV 导入，可恢复原商品 ID 和 SKU ID，不依赖标题重新匹配。
- 重复明细通过既有商品池唯一键跳过。

## 八、缓存与数据来源

正式界面现在显示以下来源状态：

- `官方实时`：非缓存且来源为官方 API。
- `本地缓存（非实时）`：读取 SQLite 缓存。
- `缓存可能失效`：目录缺失、无最后同步时间或超过 24 小时。
- `模拟数据`：Mock 来源。
- `来源/时间未知`：无法证明可信来源。

页面保留每条商品最后同步时间。官方同步失败时，界面不自动把旧缓存标成最新，
并提供“查看缓存”入口。

## 九、数据库 v3

已发布 v1 和 v2 SQL/checksum 未修改，并分别冻结校验值：

- v1：
  `6eee2177549390bc74e220626e4f7e395fd7ceb2c069389ab38994f125a3002f`
- v2：
  `9553158b15347f6c19d334091246348105f65e1547f714c7083effd065adc86f`

v3 名称：`dongxiao_catalog_consistency_v3`

新增字段：

- `dongxiao_products.presence_state`
- `dongxiao_products.missing_since`
- `dongxiao_skus.presence_state`
- `dongxiao_skus.missing_since`
- `dongxiao_skus.purchase_min_verified`

新增表 `dongxiao_catalog_sync_runs`：

- `run_id`：同步运行 ID。
- `store_id/store_app_id`：店铺隔离键。
- `source`：official/cache/mock 等来源。
- `status`：complete/partial/failed/cancelled。
- `discovered/saved/failed`：发现、保存、失败计数。
- `reconciled_products/reconciled_skus`：本次标记缺失数量。
- `errors_json`：脱敏错误摘要。
- `started_at/finished_at`：UTC ISO 8601 时间。

新增索引覆盖店铺 presence 查询和最近同步运行查询。v3 保留 v2 的非空规范化
唯一索引和店铺身份触发器，SQLite 层继续实际阻止重复商品、重复 SKU、空白
SKU、错误店铺映射和跨店复合外键。

迁移回滚：

- 迁移前由现有数据库机制生成
  `数据库文件名.pre-dongxiao-v3.bak`，重复运行会追加 `.2` 等序号。
- v3 在独立事务执行；任一步失败只回滚 v3，v1/v2 保持完整。
- 恢复时先关闭澜琅，再以迁移前 `.bak` 副本恢复数据库；不要手工修改
  `dongxiao_schema_migrations`。

## 十、性能实测

测试时间：2026-11-18 12:24:34 UTC（20:24:34 中国标准时间）

测试数据库：临时隔离 SQLite，不接触真实用户数据。

| 指标 | 1000 条 | 10000 条 |
|---|---:|---:|
| 商品+SKU+快照写入 | 552.81 ms | 5949.89 ms |
| 分页查询 500 条 | 50.93 ms | 70.90 ms |
| 多条件筛选+分页 | 6.43 ms | 21.31 ms |
| CSV 导出 | 40.48 ms | 282.83 ms |
| Python 峰值内存 | 3,351,459 B | 24,939,745 B |
| RSS 峰值增量 | 8,114,176 B | 60,813,312 B |
| 错误数 | 0 | 0 |

SKU 规则独立测量：

- 1000 条数据集：10,000 次调用共 302.47 ms，平均 30.25 微秒/次。
- 10000 条数据集：10,000 次调用共 318.72 ms，平均 31.87 微秒/次。

后台同步 UI 响应：

- 同步商品：2000 条。
- 后台同步完成时间：1658.53 ms。
- Tk 事件循环收到 107 个 tick。
- 最大 tick 间隔：19.63 ms。
- P95 tick 间隔：16.44 ms。
- 错误数：0。

以上为本次实际测量值，不是估算值。不同机器、磁盘、杀毒软件和 Python
版本可能造成波动。

## 十一、测试结果

2026-11-18 最终全量测试：

- `522 passed`
- `7 skipped`
- `1 warning`
- `18 subtests passed`
- 总耗时：72.19 秒

跳过项包括当前测试 Python 环境缺失部分 Tk 资源，以及测试账号安全凭据不可
获得的既有条件。动销专项、正式路由、权限、迁移、同步和 CSV 测试均实际执行。

静态检查：

- Ruff：通过
- `git diff --check`：通过

完整命令：

```powershell
& 'D:\11.18新项目微信\微信群控\1.8.41\项目源码\.venv\Scripts\python.exe' -m pytest -q -ra --tb=short
uvx ruff check weimi_manager/dongxiao weimi_manager/ui/dongxiao_page.py weimi_manager/wechat_api.py tools/benchmark_dongxiao_catalog.py tests/test_dongxiao_phase35.py tests/test_dongxiao_catalog_adapter.py tests/test_dongxiao_import.py tests/test_dongxiao_migrations.py tests/test_dongxiao_models.py
git diff --check
```

## 十二、修改文件清单

实现文件：

- `weimi_manager/dongxiao/application/catalog_service.py`
- `weimi_manager/dongxiao/application/import_service.py`
- `weimi_manager/dongxiao/application/pool_service.py`
- `weimi_manager/dongxiao/domain/catalog.py`
- `weimi_manager/dongxiao/domain/enums.py`
- `weimi_manager/dongxiao/domain/models.py`
- `weimi_manager/dongxiao/domain/rules.py`
- `weimi_manager/dongxiao/infrastructure/catalog.py`
- `weimi_manager/dongxiao/infrastructure/migrations.py`
- `weimi_manager/dongxiao/infrastructure/repository.py`
- `weimi_manager/ui/dongxiao_page.py`
- `weimi_manager/wechat_api.py`

测试与工具：

- `tests/test_dongxiao_catalog_adapter.py`
- `tests/test_dongxiao_import.py`
- `tests/test_dongxiao_migrations.py`
- `tests/test_dongxiao_models.py`
- `tests/test_dongxiao_phase35.py`
- `tools/benchmark_dongxiao_catalog.py`
- `tools/make_phase35_source_archive.py`

文档：

- `DONGXIAO_PHASE3_5_INTERFACE_AUDIT_CN.md`
- `DONGXIAO_PHASE3_5_REPORT_CN.md`
- `FEATURE_REGRESSION_CHECKLIST_CN.md`

## 十三、未完成与待授权

- 未使用真实店铺运行官方同步，不宣称真实平台全链路已验收。
- 未确认当前账号对商品 API 权限集 129 的实际授权。
- 未取得官方分享文本反向解析能力，自动识别商品 ID/店铺身份仍不可实现。
- 官方商品详情未提供可信起购数量，官方 SKU 的起购规则仍为“无法确定”。
- 未实现商品口令/H5 的自动解析闭环；本阶段只确认其官方生成路径。
- 商品主程序未编译、未部署、未发布。

本阶段完成后停止，不进入第 4 阶段。

# 1.6.62 本地交接记录

日期 2026-11-10。源码位于 D:\GPT\weimi_manager。

## 当前可运行交付

- 桌面原快捷方式 C:\Users\take off\Desktop\LangLang - 快捷方式.lnk 已指向 D:\GPT\builds\1.6.62-local\dist\LangLang\LangLang.exe。
- 已从原快捷方式启动并观察到 1.6.62 工作台。升级前当前账号为 zpfly，快捷方式恢复该已记住账号。
- 保留 1.6.61 的切换账号、退出登录能力。账号菜单在右上角。
- 此次为本机发行包。线上更新清单仍为 1.6.59，未发布远程更新。请不要把界面下方的远程最新版本号误判成本地运行版本。
- 旧快捷方式备份位于 D:\GPT\builds\1.6.62-local\desktop-shortcut-before-1.6.62.lnk。旧程序保留在 D:\GPT\builds\1.6.61-local\verified\LangLang。回退时先正常退出新版，再恢复快捷方式备份。

## 已修复行为

每次点击商品查询、切换商品页签或店铺，重新向微信读取当前页签的完整列表。全部店铺逐店读取，已有缓存也会联网。读取失败的店铺有明确错误提示，其旧商品不会混入本次结果。

完整且有效的响应在一个数据库事务内替换对应店铺和页签的数据。线上已消失的商品会从该页签本地结果移除。数量不一致、缺失页、重复结果、异常空列表以及取消查询均不能当作完整快照清空缓存。

修复商家后台商品状态映射，审核中 2、审核失败 3、上架策略 7 能进入正确页签。各页签仅在查询后展示当前结果数量。

删除、上下架、提交编辑、撤回审核、修改库存后重新联网查询。请求失败也重新查询，因为超时可能发生在微信已接受操作之后。只有接口确认成功才处理本地删除。详情、编辑表单和商品口令获取均读取线上数据。

附带修复已有测试暴露的 0.x 开发预览版误入生产更新检查问题。未配置专用测试渠道的开发预览版跳过更新。

## 商品页面能力及验证边界

| 功能 | 实际路径 | 条件与验证 |
| --- | --- | --- |
| 查询、重置、切店、七个页签 | 扫码 Cookie 的 shop-faas 完整分页；失败且有 AppSecret 时回退公开 API | 真实商家七页签读取成功，销售中完整分页 501 个 |
| 公开商品目录、详情 | /channels/ec/product/list/get 和 /channels/ec/product/get | 真实接口读取成功；需要 AppSecret |
| 获取口令、额度 | /channels/ec/product/taglink/get 和 getauditquota | 真实接口读取成功；额度展示仍是最近一次额度刷新结果 |
| 删除 | 公开 product/delete；仅 Cookie 时使用商家 batchDel 或回收站 batchRealDel | 路由、失败保留、成功删除通过隔离测试；没有删除真实商品 |
| 下架 | 公开 product/delisting；仅 Cookie 时使用 BatchUnListing | 路由及状态更新通过隔离测试；没有下架真实商品 |
| 上架 | 公开 product/listing | 需要 AppSecret，缺少时明确提示；未对真实商品执行 |
| 新增、编辑、撤审、库存 | 公开 product/add、update、audit/cancel、stock/update | 需要 AppSecret；读详情后编辑，提交后刷新；未进行真实写操作验收 |
| 打开后台编辑 | 既有商家浏览器会话 | 浏览器关闭后刷新店铺及商品 |
| 排序、选择、分页、导出、上架设置 | 本地界面操作 | 基于本次读取的列表，不属于平台写接口 |

本轮重点为商品管理页和相关服务链路。不能据此声称上传、托管、资金、活动等全软件业务模块已经完成真实接口验收。商家 shop-faas 属于网页后台接口，微信变更时需要重新适配。完整分页不能保证查询结束后的平台状态永远不变，下一次查询会重新读取。

## 尚未解决的跨电脑店铺同步

当前包 assets/tester_accounts.json 仍启用离线测试账号。main.py 优先创建 TesterAuthService。该登录上下文没有云同步所需的 auth_client 与 vault_key，因此 AccountStoreSync 不运行，另一台电脑新增的店铺不会自动进入此电脑。

本机真实 zpfly 账号数据库目前为 0 个店铺，已经从实际运行窗口和原生数据检查确认。用户提到的另一台电脑的 3 个店铺不在当前可访问数据中。此次修复解决有店铺数据时的商品查询，不代表这 3 个店铺已经恢复或完成同步。

后续 DeepSeek 应优先处理正式服务器认证与已有离线账号的数据迁移，检查 AuthConfig、build_auth_service、AuthContext、account_store_sync.py 以及云端账号归属。迁移前备份两端数据库，验证同账号跨机一致，不能直接关闭离线配置后让已有本地店铺落入另一个账户目录，也不能把 qweasd 的店铺移入 zpfly 冒充恢复。

## Windows 数据目录陷阱

Codex 是 MSIX 应用，其访问 C:\Users\take off\AppData\Local\LangLang 可能被重定向到 C:\Users\take off\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\LangLang。桌面原生程序使用真实 LocalAppData。相同显示路径可能实际是两个数据库，不能仅凭 Codex 进程所见的数据判断用户界面。

本轮原生检查与快捷方式升级通过当前用户 Interactive、Limited 的临时计划任务运行，脚本和无凭据结果位于 D:\GPT\builds\1.6.62-local。测试结束后任务应移除。Windows PowerShell 5 的中文脚本需要 UTF-8 BOM。应用内自动化操作使用 computer-use 技能。

## 代码位置

- weimi_manager/ui/pages.py 中 ProductsPage，实时查询、有效店铺范围、操作后刷新、实时详情、接口能力提示。
- weimi_manager/service.py 中 refresh_product_tab、sync_private_product_tab、product_write、set_products_listing。
- weimi_manager/db.py 中 replace_product_tab 和批量事务写入。
- weimi_manager/models.py 中 ProductQueryResult 和 product_matches_tab。
- weimi_manager/shop_private.py 中完整分页校验、明确成功确认。
- weimi_manager/wechat_api.py 中公开目录结构与重复分页游标检查。
- weimi_manager/ui/app.py 中开发预览版更新检查；此前账号切换修复也在此文件。
- tests/test_product_freshness.py 新增 9 个回归测试，tests/test_core.py 更新实时查询测试。
- __init__.py、pyproject.toml、uv.lock 的本地版本为 1.6.62。

## 验证与复现命令

在 D:\GPT\weimi_manager 执行

```powershell
.\.venv\Scripts\python.exe D:\GPT\builds\1.6.62-local\run-tests.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_product_freshness.py -v
.\.venv\Scripts\python.exe -m compileall -q weimi_manager
git diff --check
```

15 个测试文件独立运行，共 201 项测试，均通过。每个文件的日志及 test-results.json 在 builds\1.6.62-local。独立进程运行可避免 Tk 跨测试文件污染导致没有测试总结却退出 0 的情况。

native-live-query.json 记录真实接口经新版 SyncService 完整查询，远端 501、可显示 501、唯一 ID 501。使用真实数据库的临时副本，未对平台写入。此前七页签、目录、详情、额度、口令读取结果在 builds\1.6.61-local 下的 *audit.json。写操作仅经过隔离测试，不要描述为真实平台写入全链路已验收。

本次构建命令如下，必须使用新的构建缓存目录。

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --distpath D:\GPT\builds\1.6.62-local\dist --workpath D:\GPT\builds\1.6.62-local\work LangLang.spec
```

仓库在本轮开始前已有大量暂存、未暂存和未跟踪的修改，当前没有创建提交。不要 reset、clean 或覆盖这些修改。tools/generate_stable_release.py 使用 git archive HEAD 的路径会漏掉当前未提交修复，发布前必须处理源码快照与版本一致性。公共发布包必须排查测试账号、凭据和本地数据，不能直接上传本机目录。

## 正式自动更新发布

1.6.62 已发布到 GitHub `Zpf365/langlang-updates`，Release 标签为 `v1.6.62`，并设为 latest。

- 更新清单：https://github.com/Zpf365/langlang-updates/releases/latest/download/update_manifest.json
- 安装包：`LangLang-1.6.62-win64.zip`
- 安装包大小：67,340,505 字节
- 安装包 SHA256：`ec74005f33c74f7387146b75a8b3b15d464c17ccdff4cbbb4d4602737d7e6f1f`
- 源码包：`LangLang_1.6.62_Source.zip`
- 源码包 SHA256：`5a314ba225ef8c32cf157c8d05bbf07c87968f5fecaef676a72edb6392709565`

正式构建已从 `LangLang.spec` 排除 `tester_accounts.json` 和 `tester_channel.json`，使用 `assets/auth_config.json` 配置的正式账号服务。生成脚本同时会拒绝包含测试账号、环境文件或数据库的程序目录，并从当前工作区生成安全源码包，不再使用旧 HEAD。

已从公网更新源以 1.6.59 身份完成检查更新、下载、SHA256 校验和解压暂存验证；远端版本为 1.6.62，程序入口存在，测试账号和数据库文件均不在发行包中。

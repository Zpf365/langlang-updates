# 微信群控工程派单与合并规则

更新时间：2026-11-26（Asia/Shanghai）

本文件由统筹总工维护。模块会话只在收到明确任务后读取本文件和相关代码，不要求预先通读项目。

## 不可违反的交付规则

1. 模块会话不得执行 `git add`、`git commit`、`git merge`、`git rebase`、`git reset` 或创建/切换 Git 工作树。
2. 模块会话不得修改 `pyproject.toml`、版本文件、`update_manifest.json`、发布脚本或公共 Release。
3. 模块会话不得关闭或覆盖 `D:\WG\LangLang`，不得打最终候选 ZIP，不得发布 GitHub。
4. 只修改任务明确指定的文件。需要修改共享文件时，先停止并报告统筹总工。
5. 完成任务后先自检：定向测试、production build（仅前端任务）、`git diff --check`、改动文件清单、未验证边界。
6. 自检通过后只把结果、证据和必要的 diff 路径报告给统筹总工，不提交、不发布。
7. 未获得明确真实测试对象和写操作授权时，只做代码、离线夹具和契约验证，不使用真实账号盲测。
8. 涉及平台付款、提交订单、发货、退款、活动、佣金或商品创建时，必须保留用户确认和官方回读边界，不得伪造成功。

## 合并顺序

1. 统筹总工冻结当前版本、共享文件范围并安排唯一活跃模块。
2. 模块独立完成整条纵向功能链：UI、网络请求、官方文档研究、controller、
   service、必要的 db 迁移、夹具和测试，并自行做首轮验证。
3. 统筹总工只审查 diff、审核完成度、运行交叉测试并解决整合冲突，
   不代替模块补写遗漏的业务功能。
4. 统筹总工统一更新队列、版本、构建、候选包和最终验收。
5. 只有用户明确允许发布后，才执行本机覆盖、GitHub Release 或上架。

## 当前活动工单

### HOSTED-CENTER-001：托管中心全店联盟佣金

- 责任会话：验证托管中心功能可用性
- 独占文件：`webui/src/pages/HostedPage.vue`、`weimi_manager/webui/hosted_runner.py`
- 受限共享文件：`weimi_manager/service.py` 仅允许增加托管联盟读取方法；完成前其他模块不得修改该文件
- 测试：`tests/test_hosted_all_store_commission.py`、托管中心相关 Vitest/pytest
- 状态：已整合并签收为 `1.8.99.3` 内部候选；模块停止改动
- 要求：保存配置可启用；全店官方在售商品分批处理；不支持的功能不得伪装成功
- 首个交付：已完成并等待总控复核；未提交、未发布

### ORDER-FLOW-001：订单管理与采购整体流程

- 责任会话：订单与采购整体流程走通模块
- 独占文件：`webui/src/pages/OrdersParityPage.vue`、`webui/src/components/OrderProcurementDialog.vue`、相关订单前端子组件和新增前端测试
- 禁止修改：`weimi_manager/webui/controller.py`、`weimi_manager/service.py`、`weimi_manager/procurement.py`
- 状态：已整合并签收；模块停止改动
- 要求：从订单商品进入采购、打开官方购买页、付款后回读确认、采购记录落库、供应商发货与官方发货状态可见；缺失的 Python 接口写成精确交接清单

### PROCUREMENT-ACCOUNT-001：采购账号与结算页

- 责任会话：采购账号模块
- 独占文件：`weimi_manager/procurement.py`、`tests/test_procurement_*.py`、采购账号专用前端组件
- 禁止修改：`weimi_manager/webui/controller.py`、`weimi_manager/service.py`
- 状态：已整合并签收；模块停止改动
- 要求：淘宝/1688 独立 Profile、重启校验、失效提示、1688 结算页临时地址一次性填写的幂等和失败边界

## 等待队列

- 物流监控模块：等待采购回读和仓库物流状态接口定稿。
- 利润统计模块：等待采购实付、运费和订单数据结构稳定。
- 店铺管理、中转订单、商品管理、活动管理、登录注册与更新：按用户优先级后续派单。

### UPLOAD-PRODUCT-001：测试店真实上货闭环

- 责任会话：上传商品模块
- 独占文件：上传商品相关 Vue 页面和组件、`weimi_manager/service.py` 中商品提交方法、`weimi_manager/wechat_api.py` 中商品提交/模板方法、上传专项测试
- 受限共享文件：`weimi_manager/webui/controller.py` 仅允许修改上传商品直接相关的 RPC 接线
- 状态：真实测试店闭环通过并已整合；模块停止改动
- 要求：定位并修复 `6600120 / deliver_method`，从新页面创建最小合规测试商品，官方回读成功后只清理本次自建商品

## 真实测试店授权

- 测试店铺：`再百宽栾商行`，店铺 ID `19`，AppID `wx8248f48b5d5ba3aa`（用户口述“宽峦”，以现有店铺记录和 AppID 为准）。
- 测试账号：只允许 `zpfly`。
- 允许：正常上货、下架、删除本次测试自建商品，以及运行本项目正常业务测试。
- 禁止：色情、违法、公序良俗风险内容；任何可能导致微信扣款、封店、罚款或影响其他店铺的操作；删除或改动非本次创建的官方商品。
- 真实写入前先只读确认店铺、账号和官方当前状态；每次只做一个最小测试闭环，保留商品 ID、请求结果和官方回读证据。

## 当前串行区

以下文件和动作由统筹总工独占：

- `weimi_manager/webui/controller.py`
- `weimi_manager/webui/service.py` 的交叉修改
- `weimi_manager/db.py`
- `weimi_manager/wechat_api.py`
- `LangLang.spec`、版本文件、manifest、发布脚本
- 全量回归、PyInstaller、候选封装、本机更新和公开发布

## 1.8.99.3 集成结果

- 集成提交：`95b51f3`
- 候选：`candidate/1.8.99.3/`
- 全量 pytest：`985 passed, 7 skipped, 21 subtests passed`
- WebUI Vitest：`168 passed`
- production build、PyInstaller、隔离 EXE 登录页冒烟：通过
- 候选未覆盖本机 `D:\WG\LangLang`，未推送 Git，未发布 GitHub

模块后续规则：

1. 新的模块任务从集成提交之后的唯一基线开始，不沿用本次会话里的旧文件状态。
2. 每次派单只读取总控列出的必要文件，不要求预先通读整个项目。
3. 模块不得提交、打最终包、发布或覆盖本机；只交改动、测试和交接说明。
4. 模块的 UI、网络、controller、service、db 和测试缺口全部由该模块自行补齐；
   总控不得代为补业务代码，只做冲突安排和最终合并。
5. 需要查官方文档或第三方页面时，也由模块自行研究并写清来源；不得把研究结果
   退回总控代为实现。
6. 发现共享文件冲突时立即停止，由统筹总工安排串行写入。
7. 真实测试店仍只允许 `zpfly` 和 `再百宽栾商行`（ID 19），且只操作本次自建数据。

# 澜琅 1.9.02 桥接版本更新骨架报告

## 结论

`TASK-20261127-026` 的 A 级代码与定向验证已完成，状态为等待 CTO 复核。

- 分支：`hotfix/store-management-1.9.01.1`
- 店铺候选提交：`9b4ec45`
- 桥接骨架提交：`24a9206`
- 当前源码/内部包版本：`1.9.01.1`
- 用户可见产品版本：`1.9.01`
- 后续公共桥接版本：`1.9.02`，仍需最终集成 QA 和版本提升
- 未 push、未打包、未发布、未部署；`D:\WG\LangLang` 未触碰

## 已实现

- Ed25519 清单签名与受控公钥配置读取。
- 逐文件 SHA256、ZIP SHA256、app/backend 兼容范围校验。
- 补丁路径穿越、Windows 保留名/ADS、符号链接和禁止扩展名拦截。
- WebUI/rules 补丁 `patch_id + sequence`、rollout、paused、revoked 控制。
- 持久化撤销列表，回滚链自动跳过已撤销版本。
- WebUI active pointer 原子切换、上一版本恢复和失败回滚。
- `FrontendServer` 动态静态根，支持无需替换 EXE 的 WebUI 资源切换。
- `langlangReady` 标记、15 秒 ready 探测、失败自动恢复上一版。
- JSON rules 包框架、版本/schema/逐文件哈希及业务解析器注册表。
- 全 stable 用户后台启动更新资格；仅在内置签名信任启用时生效。
- 完整包后台校验后安全安装、全局安装锁、健康标记和健康确认前保留回滚目录。
- 内部四段版本只用于诊断/回滚；WebUI 品牌和升级提示保持三段产品版本。
- WebUI/rules 固定错误或更新不会硬阻断账户切换、退出或会话关闭。

## 已实现但当前未启用

- `assets/update_public_keys.json` 当前为 `enabled=false`、`keys=[]`、
  `channel_url=""`。
- 因此普通用户 all-stable 后台自动更新和 WebUI/rules 热包频道均保持关闭。
- 微信/第三方营销属性等业务规则只完成可签名 JSON 包框架和校验入口，
  未在未验收的情况下接管现有业务规则。

## Blocker

1. 正式 Ed25519 私钥托管、离线或 CI 签名流程尚未建立；私钥未入仓库/包。
2. 固定受控 HTTPS 补丁频道地址尚未建立，不能占用现有 GitHub Latest。
3. 需 CTO 决定密钥轮换、撤回和发布操作权限。
4. 桥接版最终必须从内部 `1.9.01.1` 提升为公共三段 `1.9.02`，
   不能公开四段版本。

## 验证

- 更新相关 Python 定向回归：`104 passed, 1 deselected`。
- 未执行全量 pytest。
- 被排除项：`test_startup_update_ui_failure_returns_to_current_version`，
  其为非交互 Tk 窗口测试；未作为本批门禁。
- WebUI Vitest：`3 files / 8 tests passed`。
- 真实 Chromium production build ready 标记：通过。
- production build：通过，`117 modules transformed`。
- 新增/改动 Python 文件 Ruff：通过。
- `git diff --check`：通过。

## 剩余风险

- 尚未生成 PyInstaller 候选，也未做候选 EXE 隔离登录页和真实改包升级测试。
- 完整包健康回滚已做单元/PowerShell 定向验证，未做一次真实跨版本替换。
- WebUI reload 已通过构建产物 ready 测试，但桌面 Qt 实际 reload 路径需在
  最终候选 EXE 中复核。
- rules 框架未接管现有业务规则，等各规则来源和消费者完成盘点后再启用。
- 未运行全量回归，最终集成 QA 仍需按发布门禁执行。

## 回滚与安全

- 本次仅新增源码、测试和候选报告，不包含数据库迁移。
- 热包和完整包状态均位于用户 `LOCALAPPDATA` 下，不修改账号或业务数据库。
- 正式频道未启用，因此当前不存在自动热更新副作用。

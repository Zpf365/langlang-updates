# 澜琅自带浏览器运行时

## 目标

- 不再依赖用户是否安装 Microsoft Edge、Google Chrome 或其他浏览器。
- 采购账号授权、1688 登录和微信店铺登录统一使用随软件携带的 Chromium。
- 保留每个账号独立的持久化浏览器配置。

## 实现

- 新增 `weimi_manager/browser_runtime.py`。
- 运行包优先查找：

```text
_internal\browser_runtime\chromium-*\chrome-win64\chrome.exe
_internal\browser_runtime\chromium-*\chrome-win\chrome.exe
```

- 开发环境允许通过 `LANGLANG_BROWSER_DIR` 指定 Chromium。
- 已移除采购、1688 和店铺网页登录中的 `channel="msedge"`。
- Playwright 启动时显式传入包内 `chrome.exe` 的 `executable_path`。
- 打包后的程序如果缺少浏览器组件，会给出明确错误，不再静默依赖系统浏览器。

## 浏览器版本

- Playwright：`1.65.0`
- Chromium：`158.0.8127.18`

## 打包

- `LangLang.spec` 会把 `browser_runtime` 内完整 Chromium 加入 `_internal`。
- `tools/build_1_8_31.ps1` 会在构建前检查 Chromium，缺失时执行：

```powershell
python -m playwright install chromium
```

- 当前测试包解压后约 650 MB，其中主要体积来自 Chromium。
- 源码压缩包不需要包含 Chromium；构建脚本会在构建机重新安装。

## 登录状态

- 采购账号继续使用独立 Profile：

```text
%LOCALAPPDATA%\LangLang\accounts\<账号目录>\data\procurement_profiles\account_<采购账号ID>
```

- 店铺网页继续使用独立 Profile：

```text
%LOCALAPPDATA%\LangLang\accounts\<账号目录>\data\edge_profiles\store_<店铺ID>
```

- Profile 目录名属于兼容路径，不代表仍依赖 Edge。

## 验证

- 自带 Chromium 单独启动成功，版本为 `158.0.8127.18`。
- 从最终打包目录中的 Chromium 启动 Playwright 成功。
- 全量测试：`420 passed, 6 skipped, 18 subtests passed`。
- Ruff：通过。
- 发布包内已确认存在：

```text
_internal\browser_runtime\chromium-1254\chrome-win64\chrome.exe
```

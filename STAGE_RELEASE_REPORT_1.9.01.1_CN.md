# 店铺管理专用热修报告 1.9.01.1

## 来源

- 公开基线：`f96a6f0` / `v1.9.01`。
- 曝光分支：`hotfix/store-exposure-1.9.01.1` / `910ee944`。
- 表格 UI 分支：`hotfix/store-table-ui-1.9.01.1` / `bb57e7b6`。
- 集成分支：`hotfix/store-management-1.9.01.1`。

## 范围

- 仅包含店铺曝光值修复与店铺表格 UI 展示。
- 不含 TASK-008/012、TASK-024、TASK-031，也不含冻结的 TASK-019。
- 保留“商品曝光（人数/次数）”完整标题。

## 验证

- 店铺 Python 定向：`91 passed, 45 deselected`。
- WebUI 展示/DOM：`6 files / 51 tests passed`。
- 真实 Chromium 几何：`4 passed`。
- production build：待最终构建。
- PyInstaller/ZIP/隔离启动：待构建。

## 发布边界

- 内部热修包，不 push、不发布、不部署。
- `D:\WG\LangLang` 保持不变。

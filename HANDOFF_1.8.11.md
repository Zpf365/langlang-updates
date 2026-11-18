# 澜琅 重构1.8.11 交接

## 发布属性

- 版本：`1.8.11`
- 类型：正式自动更新版
- 基线：`1.8.10`
- 版本分支：保持 `1.8.X`

## 根因

- 1.8.10 的全新安装 ZIP 未包含
  `_internal/assets/auth_config.json`。
- 已有安装目录保留了该公开客户端配置，所以老机器能启动；
  全新解压或覆盖安装会因认证服务未配置而拒绝启动。

## 修复

- 将现有安装目录中的公开 Supabase 客户端配置加入源码资产。
- PyInstaller 构建时把 `auth_config.json` 放入程序包。
- `tools/package_pyside.py` 新增完整包检查：缺少公开认证配置时
  直接拒绝生成安装包，避免再次发布无法启动的新包。

## 验证

- 1.8.10 ZIP 缺失 `LangLang/_internal/assets/auth_config.json`。
- 修复后构建包必须包含该文件。
- 使用全新临时数据目录启动 EXE，不产生 `startup_error.log`。

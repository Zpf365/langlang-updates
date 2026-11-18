# 澜琅 重构1.8.4 交接

## 发布属性

- 版本：`1.8.4`
- 类型：正式自动更新版
- GitHub 仓库：`Zpf365/langlang-updates`

## 本版修复

- 客户端登录调用 `public.register_my_device`。
- 新 RPC 先查找同账号同设备哈希的任意记录，包括已撤销记录。
- 已激活记录仅刷新名称、版本和最后在线时间。
- 已撤销记录在同一行重新激活，避免唯一键冲突。
- 仅有完全不存在设备记录时才执行插入。
- 账户资料行使用 `for update`，同一账号并发绑定会串行处理。

## 回归验证

- 新迁移源码回归测试通过。
- 线上函数探针确认 `lookup_ignores_revoked=false`。
- 线上函数探针确认存在 `revoked_at=null` 重新激活逻辑。
- 完整 pytest、静态检查、PyInstaller、EXE 启动和发行 ZIP 启动。

## 数据安全

- 未读取或输出真实密码、Cookie、AppSecret 或用户隐私。
- 迁移不删除设备、账户、许可证或店铺数据。
- 唯一约束 `UNIQUE (user_id, device_hash)` 保持启用。

# 澜琅 重构1.7.5 测试报告

日期：2026-11-11

## 测试环境

- Windows 桌面环境
- Python 3.11.15
- PySide6、Tk 兼容界面和 Playwright 模拟接口测试
- Supabase 项目：`ktrynxlmhheybohmucsl`

## 专项测试

- 团队服务、认证、组织同步、店铺页面和权限测试：
  - `44 passed, 2 subtests passed`
- 覆盖内容：
  - 组织角色和功能权限加载
  - 团队权限接口失败时禁止登录
  - 子账号不能自行修改密码
  - 主账号和共同主账号的完整权限
  - 普通子账号只能打开获授权页面
  - 组织保险箱推拉
  - 撤销店铺授权后的本地清理
  - 店铺刷新前先拉取组织云端店铺

## 完整回归

执行命令：

```powershell
$env:WEIMI_DISABLE_AUTO_UPDATE = "1"
$env:WEIMI_DISABLE_AUTO_BACKUP = "1"
.\.venv311\Scripts\python.exe -m pytest -q `
  --junitxml=D:\GPT\builds\1.7.5\pytest-results.xml -ra
```

结果：

- `250 passed`
- `2 skipped`
- `0 failed`
- `12 subtests passed`
- 总耗时：`40.22s`
- JUnit 记录：`264` 个测试结果条目

两个跳过项均为本机 Tcl/Tk 运行资源缺失，不是业务失败：

- `tests/test_auth.py:743`
- `tests/test_auth.py:820`

测试过程中发现并修复了自动更新测试残留定时器问题。该定时器原本会在两秒后调用
`os._exit(0)`，导致全量测试顺序执行时被提前截断；现在通过测试隔离取消定时器。

## 静态检查

- `compileall`：通过
- 本轮新增和修改 Python 文件 Ruff 检查：通过
- `git diff --check`：通过
- Edge Function 已通过 Supabase 服务端打包并成功部署

仓库原有全量 Ruff 仍有历史规则问题；本版本没有把它们伪装成全部通过，也没有进行
与本功能无关的大范围格式化。

## 服务端验证

- `team-admin` 已部署。
- 主账号列表、创建临时子账号、子账号登录、按店铺授权过滤、重置密码和停用账号
  已在临时验收账号上实际跑通。
- `ZPF:OPS-QA`、`ZPF:QA01`、`ZPF:QA02`、`ZPF:QA-OWNER` 查询结果为零残留。
- 正式 ZPFLY 组织当前只有邮箱注册的 `zpfly` 主账号。
- 组织保险箱有 1 个加密载荷。

## 未验证

- 没有连接生产店铺执行真实商品发布、上下架、删除、订单、售后、资金或活动写操作。
- 1688 链接真实采集仍依赖本机 1688 登录会话和页面稳定结构。
- 微信自动类目申请和商品草稿创建仍需隔离测试店铺验收。
- Supabase Auth 自身协议接口的改密权限仍需 Auth Hook 或自定义认证才能做服务器级
  绝对限制；当前限制已在澜琅客户端内生效。

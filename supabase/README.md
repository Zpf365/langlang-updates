# 澜琅账号与卡密服务

当前客户端直接调用 Supabase Auth 与 PostgreSQL RPC：

- 注册、邮箱确认、密码登录由 Supabase Auth 完成。
- 账户资料、设备绑定、授权状态和卡密兑换由
  `202611080002_client_rpc.sql` 中的服务端函数完成。
- 卡密生成工具通过管理员数据库连接直接写入。
- `functions/` 目录保留为后续需要 Edge Function 时的备用实现，
  当前 1.6.26 客户端不依赖已部署的 Edge Functions。

## 环境变量

在 Supabase Edge Functions 中配置：

- `CARD_HASH_PEPPER`：至少 32 字节的随机字符串。只在服务端和卡密生成工具中使用。
- `SUPABASE_URL`、`SUPABASE_ANON_KEY`、`SUPABASE_SERVICE_ROLE_KEY`
  由 Supabase 自动提供。

普通 Windows 客户端只能包含项目 URL 和 `SUPABASE_ANON_KEY`。
绝不能把 `SUPABASE_SERVICE_ROLE_KEY` 或 `CARD_HASH_PEPPER` 打包进客户端。

## 邮箱模板

在 Supabase Authentication 的邮件模板中启用数字验证码：

```html
<h2>澜琅邮箱验证码</h2>
<p>你的验证码是：</p>
<p style="font-size:28px;font-weight:700;letter-spacing:8px">{{ .Token }}</p>
<p>验证码 10 分钟内有效。如果这不是你的操作，请忽略此邮件。</p>
```

注册确认和找回密码都需要在模板中保留 `{{ .Token }}`。

## 函数

- `reserve-username`：注册前锁定账户名。
- `complete-registration`：邮箱验证后建立账户资料并发放 3 天体验。
- `login-by-username`：使用账户名和密码登录。
- `send-recovery-code`：向绑定邮箱发送找回验证码。
- `register-device`：绑定电脑，换机时扣除管理员配置的时长。
- `license-status`：返回服务端授权状态和服务器时间。
- `redeem-license-card`：兑换临时卡或永久卡。
- `admin-generate-cards`：管理员批量生成卡密。

## 客户端配置

客户端使用 `assets/auth_config.json`：

```json
{
  "supabase_url": "https://YOUR-PROJECT.supabase.co",
  "publishable_key": "YOUR-PUBLISHABLE-KEY"
}
```

该文件不包含任何服务端密钥，可以随客户端分发。

## 第一个管理员

先用普通流程注册管理员账户，然后在 Supabase SQL Editor 中执行：

```sql
update public.profiles
set role = 'admin'
where username = '管理员账户名';
```

之后运行：

```powershell
uv run python tools/generate_cards.py --count 20 --type temporary --days 30
uv run python tools/generate_cards.py --count 5 --type permanent --max-devices 999
```

工具会要求输入管理员账户名和密码。完整卡密只在生成时导出，数据库只保存哈希。

# 澜琅 1.8.49 安全审计

- Supabase Service Role Key 只在 Edge Function 环境。
- 小程序 AppSecret 只在 Edge Function 环境。
- `code2Session` 只在服务端调用。
- 不返回 session_key。
- 不保存微信密码。
- 不提取微信 Cookie。
- 配对票据只保存 SHA-256，5 分钟有效，使用后状态改变。
- 手机会话只保存哈希。
- 手机会话不包含商家 OpenAPI Token。
- 买家身份与商家店铺身份分表。
- 手机用户确认不标记购物车核验成功。
- 真实订单必须通过官方商家订单接口核验。
- 不自动付款、退款或修改订单。
- RLS 限制桌面账号只能读取自己的任务和订单。
- Edge Function 对任务和订单再次校验 owner/buyer identity。
- Mock 数据不写入真实订单匹配。


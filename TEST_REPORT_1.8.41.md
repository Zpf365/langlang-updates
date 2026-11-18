# 澜琅 1.8.41 测试报告

- 定向 Python 测试：34 passed，1 skipped。
- 全量 pytest：443 passed，6 skipped，18 subtests passed。
- Android debug 单元测试：通过。
- Android release 单元测试及签名 APK 构建：通过。
- APK 版本：1.2.0，versionCode 3。
- APK 使用与 1.0.0 相同的发布证书，可在现有安装上覆盖更新。
- Supabase 生产迁移 `202611170001_mobile_sales_wechat_shop_queue.sql`：已应用。
- Supabase 生产迁移 `202611170002_mobile_sales_raw_wechat_link.sql`：已应用。
- Supabase 生产迁移 `202611170003_mobile_sales_owner_list_tasks.sql`：已应用。
- Supabase 生产迁移 `202611170004_mobile_sales_owner_list_volatility.sql`：已应用。
- Supabase 数据库 lint：无本次函数新增错误；仅保留两个既有未使用变量警告。
- 手机实机覆盖安装：通过，版本已显示 1.1.0。

## 尚未验证

- 手机处于锁屏状态，尚未完成微信商品加购的最终实机流程。
- 解锁后需要用一条任务验证搜索、发送、规格、数量、加购和返回状态。

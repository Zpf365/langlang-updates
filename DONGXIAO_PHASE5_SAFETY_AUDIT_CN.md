# 动销中心第 5 阶段安全审计

## 1. 数据安全

- 数据库沿用在线备份 API，迁移前创建 SQLite 一致性备份。
- 实盘检查执行 `PRAGMA integrity_check`。
- 实盘检查把当前数据库复制到同名目录内的临时文件，恢复后执行完整性
  检查，再删除临时副本。
- v6 迁移失败只回滚自身事务，v1～v5 保持完整。
- 历史订单、售后、任务、审批和审计按 `store_app_id` 可读。
- 已删除店铺不能继续执行平台写操作。
- 审计表有只追加触发器。
- 运行期平台写开关变更追加审计记录。

## 2. 店铺隔离

- 商品、SKU、商品池、任务、订单引用、售后、审批和审计均保存
  `store_app_id`。
- 服务层验证 `store.id` 与 `store.app_id` 一致。
- 平台客户端由 `SyncService.client_for(store)` 按店铺创建，不允许共享
  Token 或 Secret。
- 调度器按 `store_id` 限制并发。
- 跨店铺商品池、任务和历史读取测试通过。

## 3. 任务安全

- 任务和全部执行明细单事务原子创建。
- 同一幂等键相同请求返回原任务。
- 同一幂等键不同内容拒绝。
- 本地安全任务异常可按有界指数退避重试。
- 平台写任务异常进入 UNKNOWN。
- UNKNOWN 必须先远端核验，禁止自动重放。
- 批量执行异常会处理所有已领取明细。
- 任务租约领取和续约写入心跳。
- 过期租约按执行模式恢复。
- 强制退出测试确认平台写 RUNNING 明细进入 UNKNOWN。
- 多店铺并发、页面切换和调度器长期运行测试通过。

## 4. 资金安全

- 所有金额使用整数分。
- 金额上下界和布尔值误用被拒绝。
- 审批金额不能超过官方申请金额。
- 退款余额计算包含已退、在途和其他审批占用。
- 官方同意/拒绝请求没有金额参数，因此禁止部分退款执行。
- 平台执行必须存在已通过审批。
- 同一售后只允许一个待审批记录。
- 过期审批不能决定。
- 平台执行必须经过权限检查、人工确认、幂等键和写后回查。
- 平台写操作总开关默认关闭。
- 写后无法证明操作成功时保持 UNKNOWN。

## 5. 凭据与日志

- 稳定 Token 只保存在客户端内存。
- 日志和 API 观测不保存请求/响应 body。
- 完整 URL 替换为 `[URL]`。
- access_token/token/AppSecret/secret/password/cookie/authorization 参数值
  替换为 `[REDACTED]`。
- Bearer 凭据替换为 `Bearer [REDACTED]`。
- API path 不包含 query。
- 候选包不包含测试账号、通用登录、`.env`、SQLite 数据库或 AppSecret。
- 候选包包含 Supabase client 的 `publishable_key`，该值属于公开客户端配置，
  不是服务端 Secret 或 AppSecret。

## 6. 权限

应用服务层而非仅页面按钮执行以下检查：

- `dongxiao_aftersale_request`
- `dongxiao_aftersale_approve`
- `dongxiao_aftersale_execute`
- `dongxiao_task_verify_unknown`
- `dongxiao_platform_write_switch`
- `dongxiao_preflight_run`
- `dongxiao_preflight_platform_read`

无权限测试通过。真实平台只读检查除权限外还要求精确的本次授权确认文本。

## 7. 实盘操作边界

- 本次没有连接真实店铺。
- 本次没有执行真实售后同意、拒绝或退款。
- 未编译或部署到 `D:\WG\LangLang`。
- 未发布、未更新自动更新清单。
- 未修改 Supabase migration、RPC、Android 或登录授权系统。
- 候选 EXE 位于源码工作区的隔离 artifacts 目录。

## 8. 残余风险

1. 真实平台 Token 权限、限流和接口返回仍需授权后在线验收。
2. 真实同意/拒绝后的目标状态需要在测试售后单中人工执行。
3. 原始微信小店分享文本仍没有可确认的官方反向解析能力。
4. Tk 主线程最大间隔在压力运行中约 31～36 ms，虽不影响操作，但仍应继续
   监控低配置设备表现。
5. 候选 EXE 登录后的主工作台没有测试账户可用，只能由用户在场完成最终
   打包形态验收。

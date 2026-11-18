# 澜琅 重构1.8.4

本版本发布到正式自动更新渠道。

- 修复已撤销电脑重新登录时报
  `duplicate key value violates unique constraint
  "devices_user_id_device_hash_key"` 的问题。
- 登录实际调用的 `register_my_device` RPC 现在会读取已撤销设备记录，
  重新激活原记录，不再重复插入。
- 同一账号同一电脑的在线重复登录会直接刷新设备状态。
- 换机或重绑仍按账户设备上限和许可规则处理。
- 保留 1.8.2 的云端命令授权、店铺事务回滚、跨电脑同步和后台任务优化。

此前的 `register-device` Edge Function 修复没有覆盖客户端实际调用的
数据库 RPC；本版修复的是真实登录路径。

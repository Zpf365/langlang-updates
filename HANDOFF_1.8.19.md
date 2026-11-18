# 澜琅 1.8.19 交接说明

## 修复与升级内容

- `_load_snapup_activities()` 不再把 `snapup_activities` 作为云端权限命令传递。
- `_renew_selected_snapup()` 不再把 `renew_snapup_activity` 作为云端权限命令传递。
- 正确的授权仍由 `AuthorizedServiceAdapter` 和
  `AuthorizedWebSessionAdapter` 使用 `activities.manage` 完成。
- 创建活动改用商家后台 `createSnapUpTaskV2`，每个店铺提交一场多商品活动。
- 使用 `batchGetProducts` 获取后台真实商品、SKU、库存和状态。
- 只收录后台状态为销售中、SKU 可售、库存大于 0 且价格规则有效的商品。
- 单场活动上限为平台规定的 200 个商品。
- 删除活动使用 `deleteSnapUpTaskV2`；进行中活动先调用停止接口。
- 商品预览改为分页渲染，支持 50、100、200、500 条每页。

## 根因

后台任务层的 `command` 参数只接受权限命令，例如 `activities.manage`。
1.8.18 错误地把业务方法名传给该参数，因此在任务启动前触发了
“客户端不支持业务命令”。

## 兼容说明

- 正式界面和 1.8.18 完全一致。
- 数据库结构没有变化。
- 限时活动规则和续期完成提示保持不变。

## 真实环境验证

- “绝巧努胜小店”已创建活动 `253022067`，包含 87 个有效商品。
- “环罗努胜小店”已创建活动 `252876761`，填满 200 个商品。
- 两个店铺原有旧活动均已删除，并在删除前确认新活动创建成功。

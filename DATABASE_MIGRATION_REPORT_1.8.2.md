# 澜琅 重构1.8.2 数据库迁移报告

日期：2026-11-12

## 本地 SQLite

- 本次没有删除或重建现有 SQLite 表。
- 沿用 1.6.63 数据库路径、schema 和 DPAPI 凭据格式。
- 新增代码只调用现有表和索引，读取时保留旧数据。
- 事务失败回滚通过现有 `stores`、`store_groups` 表恢复，不更换本地数据库文件。

## Supabase

已通过 CLI 对项目 `langlang` 执行：

```text
202611120005_store_organization_permissions.sql
```

验证结果：

```text
automation.manage  products_edit  store_scoped=false
stores.delete      stores_delete  store_scoped=true
stores.organize    stores_edit    store_scoped=false
```

本版还依赖此前已部署的组织店铺保险箱、命令授权、订单工作台和组织设置表。

## 安全与兼容

- 未导出任何真实 AppSecret、Cookie、账号密码或用户隐私。
- 云端店铺保险箱和组织设置由服务端加密保存。
- 店铺删除使用 tombstone，防止旧电脑重新上传已删除店铺。
- 同一 AppID 在删除后可创建新的店铺记录。

## 回滚

- 客户端回滚到 1.8.1 不要求撤销新表；新增表对旧客户端无影响。
- 服务端函数为 `create or replace`，可用前一版迁移中的函数定义恢复。
- 分享包不覆盖线上自动更新，回退时重新使用 1.8.1 程序目录即可。

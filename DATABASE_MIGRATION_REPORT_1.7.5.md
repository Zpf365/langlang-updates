# 澜琅 重构1.7.5 数据库迁移报告

日期：2026-11-11

## 迁移文件

- `supabase/migrations/202611110001_organizations.sql`

## 迁移前保护

- 建立 `public.migration_backup_20261111_profiles`。
- 备份前原有账户资料为 7 行。
- 迁移只新增组织相关表和函数，没有重建或删除原账户、店铺、订单或本地数据库表。

## 新增结构

- `organizations`
- `organization_members`
- `organization_store_grants`
- `organization_vaults`
- `organization_audit_logs`

## 安全措施

- 所有组织表启用 Row Level Security。
- 客户端只能通过受控 RPC 读取组织上下文、团队成员和过滤后的保险箱。
- 组织保险箱使用 PostgreSQL `pgcrypto` 加密。
- Edge Function 使用服务端密钥访问成员管理表，不把服务端密钥下发到客户端。
- 服务端禁止共同管理员降级或停用真正的团队主账号。

## 当前验证结果

- `backup_profiles`: 7
- `organizations`: 7
- `organization_members`: 7
- `organization_store_grants`: 0
- `encrypted_vaults`: 1
- `organization_audit_logs`: 7
- 四个临时 QA 账号：0 残留
- 正式 ZPFLY 组织：仅 `zpfly`，角色 `owner`

## 数据兼容策略

- 旧账户保持原组织所有者身份。
- 旧单用户 metadata 店铺保险箱仍保留，不执行删除。
- 有正式组织的账户使用组织保险箱，并在刷新时合并各电脑新增店铺。
- 子账号只获得授权店铺，撤销授权后下次刷新清理本机绑定。
- 共同主账号不会直接合并自己的已有店铺保险箱，避免静默覆盖数据。

## 回退方式

- 应用回退：重新安装上一稳定版 EXE；上一版本不调用组织 RPC。
- 服务端回退：停止新版客户端后删除 `team-admin` 部署。
- 数据库结构为新增式迁移。确需完全回退时，可删除新增 RPC、Edge Function 和五张
  组织表；账户原始资料仍保存在 `migration_backup_20261111_profiles`。
- 当前没有删除旧账户、旧店铺数据库或旧单用户保险箱，因此回退不需要恢复本地用户
  数据。

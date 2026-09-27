# 税务稽查案件与复议流程

纯Python标准库实现的税务稽查案件与复议流程原型，使用SQLite持久化，HTTP接口由`http.server`提供。

## 模块结构

- `app.py`：命令行参数、依赖组装和服务启动。
- `src/domain.py`：领域数据类型、错误和基础校验。
- `src/delivery.py`：送达台账资料校验、生效判定与详情汇总。
- `src/rules.py`：状态转换、补税、滞纳金、处罚和证据完整性和冲突检查。
- `src/repository.py`：SQLite建表、事务和查询。
- `src/service.py`：用例编排、权限检查、乐观并发和审计。
- `src/http_api.py`：HTTP路由与统一错误响应。
- `src/audit.py`：事件时间线。
- `static/index.html`：最小演示页面。
- `tests/`：完整流程、规则计算和失败场景测试。

## 启动

```bash
python3 app.py --db ./data.db --port 8326
```

默认端口为`8326`，默认数据库位于项目目录。服务启动时自动建表。

## 主要接口

- `GET /health`：健康检查。
- `GET /`：演示页面。
- `GET /api/records`：记录列表，可带`state`和`limit`参数。
- `GET /api/records/{id}`：记录详情。
- `GET /api/records/{id}/audit`：审计时间线。
- `GET /api/records/{id}/deliveries`：送达台账详情（当前送达、生效日、复议期限、待办、历次补正、完整历史）。
- `POST /api/records/{id}/deliveries`：登记送达，请求体为`{"data":{...}}`。
- `POST /api/records/{id}/deliveries/correct`：补正送达，请求体为`{"data":{"reason":"...", ...}}`；原记录保留并标记为被补正。
- `GET /api/stats`：状态统计。
- `POST /api/records`：创建记录，请求体为`{"reference":"...","data":{...}}`。
- `POST /api/records/{id}/actions/{action}`：执行业务动作，请求体为`{"expected_version":1,"data":{...}}`。

## 送达规则

稽查决定必须登记送达后才能结案，复议期限自送达生效日起算（默认60日）。

- 送达方式：`direct`直接签收、`mail`邮寄回执、`announcement`公告送达。
- 直接签收/邮寄回执必须提供`voucher_date`凭证日期，生效日即凭证日期。
- 公告送达只能在无法直接送达时使用，必须提供`announcement_date`与`unavailable_reason`；自公告之日起满三十日的次日生效。
- 同一文书（`document`）只保留一条`active`有效送达；补正时原记录置为`superseded`并保留，新记录通过`supersedes_id`关联并记录补正原因。
- 送达未生效时`close`/`appeal`动作返回409；登记/补正限`inspector`、`reviewer`、`admin`。

登记送达示例：

```json
{"data":{"document":"稽查决定书","method":"direct","voucher_date":"2026-09-27"}}
```

公告示例：

```json
{"data":{"document":"稽查决定书","method":"announcement","announcement_date":"2026-09-01","unavailable_reason":"纳税人下落不明"}}
```

除`/health`和`/`外，请求需提供`X-User-Id`、`X-Role`，可选`X-Org`。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖完整流程、规则计算、重复引用、权限拒绝和版本冲突。

# 税务稽查案件与复议流程

纯Python标准库实现的税务稽查案件与复议流程原型，使用SQLite持久化，HTTP接口由`http.server`提供。

## 模块结构

- `app.py`：命令行参数、依赖组装和服务启动。
- `src/domain.py`：领域数据类型、错误和基础校验。
- `src/rules.py`：状态转换、补税、滞纳金、处罚和证据完整性和冲突检查。
- `src/delivery.py`：送达台账，资料校验（DeliveryForms）与生效/复议/结案判定（DeliveryRules）分开。
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
- `GET /api/records/{id}`：记录详情，内嵌送达摘要（当前送达、生效日、待办）。
- `GET /api/records/{id}/deliveries`：送达台账详情：当前送达、生效日、待办和历次补正。
- `POST /api/records/{id}/deliveries`：登记送达，请求体为`{"data":{...}}`。
- `GET /api/records/{id}/audit`：审计时间线。
- `GET /api/stats`：状态统计。
- `POST /api/records`：创建记录，请求体为`{"reference":"...","data":{...}}`。
- `POST /api/records/{id}/actions/{action}`：执行业务动作，请求体为`{"expected_version":1,"data":{...}}`。

## 送达规则

- 送达方式三种：`direct`（直接签收，传`voucher_day`签收日期）、`mail`（邮寄送达，传`voucher_day`回执日期）、`notice`（公告送达，传`notice_day`公告日期和含“无法直接送达”的`reason`，仅限无法直接送达时使用）。
- 直接签收/邮寄以凭证日期为生效日；公告自发布日满三十日的次日生效，期内视为未生效。
- 送达生效前不能结案；复议申请传`appeal_day`（YYYY-MM-DD），期限从生效日起算60日（即建件时的`appeal_deadline_day`）。
- 同一文书只保留一条有效送达：已有有效送达时必须带`corrects_id`和`correction_reason`按补正登记，旧记录置为`superseded`并完整保留；台账`corrections`返回历次补正。

除`/health`和`/`外，请求需提供`X-User-Id`、`X-Role`，可选`X-Org`。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖完整流程、规则计算、重复引用、权限拒绝和版本冲突。

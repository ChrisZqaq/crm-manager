# CRM Manager 文档入口

更新：2026-10-13。第一版核心及获批工作台本轮修改已完成分阶段独立验收，最终应用c29ca9a、发行5218025；本机与云端IP测试已部署，最终真实登录/新接口/静态烟测通过。当前进入用户试用，未正式业务切换、未导真实期初；最新状态见开发计划。

## 所有人最小阅读集

根目录[AGENTS](../AGENTS.md) → [协作与会话ID](project/COORDINATION.md) → [当前任务](project/DEVELOPMENT_PLAN.md) → 自己的ROLE/WORK。按任务加载相关业务/规格，不默认读全部docs、其他角色笔记或历史。

| 角色 | 职责/阅读入口 | 当前角色记录 |
| --- | --- | --- |
| 负责人 | [协作](project/COORDINATION.md)／[当前问题](project/ISSUE_LOG.md) | [开发计划](project/DEVELOPMENT_PLAN.md) |
| UI | [ROLE](workspaces/ui/ROLE.md) | [WORK](workspaces/ui/WORK.md) |
| 前端 | [ROLE](workspaces/frontend/ROLE.md) | [WORK](workspaces/frontend/WORK.md) |
| 原开发/后端 | [ROLE](workspaces/backend/ROLE.md) | [WORK](workspaces/backend/WORK.md) |
| 测试 | [ROLE](workspaces/qa/ROLE.md) | [WORK](workspaces/qa/WORK.md) |
| 部署工程师 | [ROLE](workspaces/deploy/ROLE.md) | [WORK](workspaces/deploy/WORK.md) |

## 公共依据：负责人维护，角色按需读取

- **business/**：[项目共识](business/PROJECT_CONSENSUS.md)、[商品](business/PRODUCT_CATALOG.md)、[采购](business/PURCHASE_RECEIVING.md)、[销售履约](business/SALES_FULFILLMENT.md)、[权限](business/ACCESS_CONTROL.md)、[客户/报表](business/CUSTOMERS_REPORTS.md)、[期初/导出](business/INITIAL_DATA.md)。
- **specs/**：[PRD](specs/PRD.md)、[SPEC](specs/SPEC.md)、[数据库](specs/DATABASE_DESIGN.md)、[成本算法](specs/COST_ENGINE.md)、[API](specs/API_CONTRACT.md)、[验收](specs/ACCEPTANCE.md)。业务基线ad06ea3已批准，后续明确增量见对应文档。
- **project/**：只维护当前会话、任务、问题和授权，不存角色聊天全文。
- **workspaces/**：ROLE/WORK和角色产物，不是已确认业务的副本；原型不等于批准、交付声明不等于验收。
- **templates/**：[期初模板](templates/期初盘点模板.xlsx)，用户已有未提交修改不随整理提交。
- **archive/**：[旧阶段记录](archive/DEVELOPMENT_PLAN_HISTORY.md)、[旧问题证据](archive/ISSUE_LOG_HISTORY.md)、[旧协作记录](archive/COORDINATION_HISTORY.md)、[文档审查](archive/DOCUMENT_REVIEW.md)、[协作方法参考](archive/PROJECT_WORKING_METHOD.md)。仅查证具体历史时读取。

应用代码仍在crm/config，脚本在scripts，不因资料分层改变。数据库、图片、运行时凭据和临时测试资产放忽略目录.local/，不提交Git。

## 本地开发命令

固定依赖由pyproject.toml/uv.lock维护。本地准备与启动：

```sh
uv sync --locked
scripts/install-dev-postgres.sh
scripts/dev-db.sh start
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver 127.0.0.1:8000
```

这组命令面向默认开发库，不是演示库或生产部署。用户本机演示配置和重启方法见[当前计划](project/DEVELOPMENT_PLAN.md#本机演示供用户试用)。只清理自己启动的服务。

用户试用入口与操作顺序见[第一版测试指引](project/USER_TEST_GUIDE.md)。本机现有试用数据保留，云端另用合成测试数据，二者不自动同步。

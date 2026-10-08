# 原开发／后端当前工作

更新：2026-10-08；UI-QUERY-1应用实现及自测完成，已固定本地提交，待交QA。长期主责后端和数据库。

最近阶段交付：阶段6fd3c991及导航修复b6bc1e8已负责人验收，发布f477c07；UI-QUERY-1待提交，结果见下节。

已向前端说明现有权限、同源session/CSRF、分页及订单preview/确认幂等流程；仅接口事实，不是原型批准或改业务授权。若后续需后端改动，报告负责人决定范围和前后端先后写入。不得移动公共文档、用户期初模板或停止演示服务。

2026-10-08 UI-IMPLEMENT-6已派前端为唯一应用写入者。后端本轮恢复只读接口答疑及自己的WORK记录，不编码；需后端依赖则报告负责人明确范围/优先级及前端停写交接后再实施。

## UI-DASHBOARD-DEPENDENCY｜只读核查（实施派发前快照）

依据：`docs/workspaces/ui/WORK.md` 唯一审批栏、`static-v3/HANDOFF.md` 工作台/API范围、`docs/specs/API_CONTRACT.md` 报表与权限口径。static-v3六页已获批，前端是唯一应用写入者；本项仅查证、记录和答复接口事实。

### 现有来源

- `/` 由 `home()` 转到商品目录；未发现 dashboard/workbench 页面或聚合 API。最小页面入口候选是复用 `/` 并让 `home()` 渲染工作台，不另造页面路由。
- `GET /api/v1/reports/sales` 有服务端 `summary.totals.revenue_amount`。已交付销售按快递发货时间或现场交付时间统计，日期使用 `Asia/Shanghai`；行按SKU、仓库、销售、客户、渠道和地区分组，不是日趋势。
- `GET /api/v1/orders?status=ready_to_ship&page_size=1` 可复用服务端 `total` 作为待出库条数；该列表只支持 `q/status`。`q` 搜客户快照名、渠道、销售用户名，不搜订单编号；没有仓库、销售、日期或收款状态筛选。
- `GET /api/v1/reports/payments?period=all&status=pending&page_size=1` 返回待核对付款的订单行和服务端 `total`，`summary` 为空。付款记录状态 `pending/problem/passed` 分别是待核对、待处理、核对通过；日期依据是首次登记收款凭证时间，不能当订单日期。
- `GET /api/v1/reports/inventory?low_stock=true&page_size=1` 可复用服务端总数及明细。`inventory_rows` 已按全局提醒开关、SKU提醒开关、有效阈值和可售量判低库存；行粒度是仓库×SKU，含 `unit/on_hand/available/occupied/reserved/hoarded/damaged/cost_amount/average_cost`。报表 `summary` 为空，没有单位汇总；箱、盒、包、个不能直接相加。
- 付款列表/订单详情读取和核对写入权限分开。核对使用 `POST /api/v1/orders/{id}/verify-payment`，需 `verify_payment`、账户责任匹配及版本校验；报表可读不授予核对操作权。
- 客户详情 `GET /api/v1/customers/{id}` 只有资料与地址，没有最近订单。订单 `q` 无法按客户FK精确匹配。

### 最小数据/接口建议

- 四张摘要卡优先复用上述查询：北京时间当日自定义区间的已交付销售额、`ready_to_ship` 订单数、待核对付款数、已启用低库存提醒下的低库存仓库×SKU记录数。浏览器只显示服务端汇总/`total`，不自行汇总。
- 工作台的轻量趋势目前没有数据源。最小增量是在现有销售报表查询增加可选的服务端日序列（按已批准销售金额、同一发货/现场交付口径及北京时间；沿静态图近7天展示），不改既有默认响应，也不在浏览器逐日计算。缺省期零值须来自有效查询；无数据源仍标“数据待接入”。
- 待办列表可复用订单列表、收款报表；提成待复核可查现有提成报表，但必须保留 `view_all_commissions` 行范围，操作仍看单独权限。图中的“客户待跟进”未发现已批准字段、待办模型或接口，不应造合成真实待办；需要负责人确认或暂标数据待接入。
- `/api/v1/reports/sales` 日期过滤当前按发货/现场交付；订单列表图只画起止日期，没有说明按下单时间还是履约时间。此含义需负责人确认，不能把报表日期口径套给订单列表。
- 未发现 `view_dashboard` 权限或工作台页面准入规则。工作台各数据块必须沿用各来源权限及本人/全部范围；页面是否面向任何已登录用户、以及无权数据块如何呈现，需负责人确认，不能默认授予全员汇总数据。
- 客户最近订单的最小候选是扩展 `GET /api/v1/orders` 的精确 `customer_id` 过滤；保留订单本人/全部范围，并在客户详情场景校验客户可见范围。现有订单列表筛选需在服务端分页前执行，不能浏览器筛当前页。

### 若负责人后续派发后端依赖

- 工作台：优先复用现有 `/` 路由和销售报表 API；若确认采用日序列，改 `crm/reports.py` 与 `crm/views.py`，无需新模型/迁移或新增首页URL。若选择单一聚合API，再由负责人明确其权限响应规则及是否加 `config/urls.py` 路由。
- 订单列表/客户最近订单：集中扩展 `crm/views.py` 现有 `orders_api` 查询，并补现有测试文件中的过滤、权限、分页回归；不新建平行查询API。API契约变更由负责人更新公共规格。
- 验收重点：每日趋势点与同口径单日销售报表一致；四摘要与原查询服务端 `summary/total` 一致；低库存遵守提醒开关和阈值；未授权范围无数据泄露；订单筛选先过滤再分页、客户ID精确匹配、各日期依据明确；收款核对端点继续执行权限/账户责任/版本检查。

未决：工作台准入/区块权限表现、订单列表日期含义、“客户待跟进”是否只是示意、是否批准增加服务端销售日序列。已向前端回报现有接口和限制；未改应用/API/配置，未运行应用测试。等待负责人安排前后端停写交接后再编码。

## UI-QUERY-1｜实现与自测

依据：负责人固定起点`4acccd79bd0825afc9f8211c68f15a3bba31184e`及最新UI/API增量。前端已在`4e4890e08a6a5c5b7ffa756f598e94d35a6dbc54`停写；本轮后端为唯一应用写入者。负责人另行维护的公共契约/计划/问题文档保持未暂存，不纳入代码提交。

### 已实现

- `GET /api/v1/orders`保留`q/status`及原排序分页；新增精确`customer_id`、活动商品行`warehouse_id`（去重）、`salesperson_id`、付款凭证`verification_status=pending/problem/passed`、成对`start_date/end_date`（北京时间按`created_at`含首尾）。数字`q`同时匹配现有展示`sequence`和订单ID。筛选均在`total`和分页前完成；客户过滤同时校验客户详情权限/可见范围与订单可见范围；无收款记录的订单不匹配核对状态。
- `/`对登录用户渲染已有`crm/workbench.html`，不改路由或模板；首页只呈现模板现有权限门控区块，不新增预读或聚合权限。
- 销售报表接受`series=day`及自定义日期范围，顶层返回零填充`series:[{date,revenue_amount}]`，使用同一已交付订单/活动行/授权筛选集，日金额为3位小数字符串，范围最多366天；缺省请求和`summary.totals`保持兼容。
- 库存报表`summary.by_unit`按售卖单位分组返回`on_hand/available/occupied/reserved/damaged`，基于当前账号授权且已应用筛选的全量行、不受分页截断影响、不跨单位相加；低库存提醒仍由现有全局/SKU开关和阈值判定。
- 无模型迁移；没有修改`config/urls.py`、模板或静态资源。

### 验证

- Python 3.12语法编译及`git diff --check`通过；Django system check在测试启动时通过。
- 隔离测试运行时：`/Users/chrisz/.cache/aluola-crm-uiquery1-venv`；PostgreSQL本地Unix socket `55439`，Django临时测试库名`test_aluola_crm_uiquery1`。测试运行器创建并销毁该新库；没有使用或删除`test_aluola_crm_dev`及演示库，没有启动Web服务。
- 定向回归7项通过：订单筛选/客户与订单双范围/同名客户/多仓去重与历史作废行/日期边界/权限撤销/付款无凭证边界，另含日报表序列及单位汇总、低库存开关。
- 阶段6报表及日期准则和原订单状态筛选共12项通过。

命令（均以`CRM_DB_NAME=aluola_crm_uiquery1`、`CRM_DB_HOST=/Users/chrisz/Documents/projects/aluolaCRM/.local/postgres-socket`、`CRM_DB_PORT=55439`、`UV_PROJECT_ENVIRONMENT=/Users/chrisz/.cache/aluola-crm-uiquery1-venv`及固定Python 3.12运行）：

1. `uv run --locked --python /Users/chrisz/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 manage.py test crm.test_phase4.OrderListFilterTests crm.test_phase6.ReportPhase6Tests.test_sales_groups_order_lines_per_unit_warehouse_and_snapshot_region crm.test_phase6.ReportPhase6Tests.test_inventory_report_keeps_live_balance_and_hoard_reason`（7项通过）。
2. `uv run --locked --python /Users/chrisz/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 manage.py test crm.test_phase4.OrderListStatusTests crm.test_phase6.ReportDateCriteriaTests crm.test_phase6.ReportPhase6Tests`（12项通过）。

### 交接状态

应用改动已由本地提交固定；测试后未再改应用代码。下一步将完整命令/范围/限制发负责人及QA后停止应用写入。不推送。前端仍需接入趋势响应及库存单位汇总；当前静态模板尚未显示这两项。

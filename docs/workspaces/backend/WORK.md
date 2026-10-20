# 原开发／后端当前工作

更新：2026-10-20；COST-COMMISSION-BYPASS-1020最小修复已固定，待QA定向复核。长期主责后端和数据库。

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

## UI-DATE-OVERFLOW｜日期边界修复与自测

依据：负责人完成 QA 后的 UI-DATE-OVERFLOW 派发；本轮后端重新取得唯一应用写入权。只改共享日期边界、回归测试和本工作记录。

- `_date_criteria` 计算 `end_day + 1` 时捕获 `OverflowError`，转为现有 `OperationError`（HTTP 400、`invalid_input`），错误归属 `fields.end_date`。订单列表和报表共用此校验；未改端点异常处理、日期截断、业务口径或 API 成功响应结构。
- 回归覆盖：订单列表 `9999-12-31` 返回 400；共享日期函数把最大日期映射为 `end_date` 错误；销售报表最大日期返回 400；闰年 2024 全年 `series=day` 返回 366 点。
- 定向自测4项通过（订单日期API、日期准则类2项、销售报表API/序列），Django system check 无问题。使用 Python 3.12 专用环境 `/Users/chrisz/.cache/aluola-crm-date-overflow-20261008-venv` 和独立 PostgreSQL 测试库 `test_aluola_crm_date_overflow_20261008`；测试运行器已销毁新建测试库，未使用或删除 `test_aluola_crm_dev` 或演示库。
- 不需要数据库迁移。修复提交后将完整证据交负责人和 QA；不推送，并停止应用写入，待后续派发。

## UI-OWNER-FILTER｜只读依赖核查

依据：负责人补充的 UI-OWNER-FILTER 核查请求；前端是唯一应用写入者。本轮只读查证，记录后端最小方案，不改应用或公共契约。

- `GET /api/v1/orders` 已有精确 `salesperson_id`，先按 `sales_order_queryset_for(actor)` 限定本人/全部订单范围，再过滤并分页；没有通用销售用户列表 API。`GET /api/v1/customers` 没有 `owner_id` 筛选，但每条客户数据有 `owner_id/owner_name`。`owner_id` 仅用于客户创建/转交写操作。
- `customer_form_page` 仅在客户表单页面按权限注入启用销售员；报表内部 `build_report.filter_options.salespeople` 由可见订单派生且报表 API不返回它们。都不适合作为两张列表共同依赖的完整筛选数据源。
- 最小实现建议：前端停写后再由负责人派发，限 `crm/views.py` 的 `customers_api` / `orders_api`、`crm/test_phase4.py` 和负责人维护的 API 契约。原列表响应各增加同形 `filter_options.salespeople:[{id,label}]`；客户选项只从权限过滤后的客户集合 distinct `owner_id/owner__username` 生成，订单选项只从权限过滤后的订单集合 distinct `salesperson_id/salesperson__username` 生成。两者标签用 username，各自独立、不交叉复用；选项先于搜索/筛选和分页从全量可见集合计算，不用当前页、不从全系统用户表泄露无关用户。客户集合保留现有归档筛选语义。
- 客户新增 `owner_id` GET：先建立 `customer_queryset_for(actor)` 权限范围，再按既有 `identifier` 校验并过滤，之后才计算 `total/page`；无查看全部权限者传其他owner ID只得到范围内空结果，不因参数扩大权限。无效ID沿用现有 400 字段错误。订单筛选无需新增参数或权限。
- 最小回归边界：客户全部范围含至少两位owner且 `page_size=1` 时，完整选项仍列全且带姓名；owner_id精确过滤后items和total吻合；普通销售不能通过其他owner ID或选项看到范围外客户/用户；无效ID返回400。订单选项覆盖当前用户可见订单中的多位销售且不受分页截断，普通销售只得到本人选项；保留现有 `salesperson_id` 过滤回归。
- 当前未实现、未跑测试，等待负责人停写交接后另行派发；本工作仅为只读核查和技术建议。

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

## UI-LIST-FILTERS｜实现与回归（2026-10-08）

依据：负责人冻结的六页 API 契约及明确派发；前端已在 `17870126ab7ff0aec079cf800fbc264626f481d8` 固定并停写。本轮只改授权的应用和现有测试文件，不改模板、模型、路由、权限及负责人维护的公共契约，不推送。

### 完成内容

- `GET /api/v1/customers` 在当前权限和归档范围内、搜索及分页前计算销售员选项；新增精确 `owner_id` 过滤和原有格式的无效 ID 错误。
- `GET /api/v1/orders` 从全部授权可见订单及活动商品行计算销售员、仓库选项，在筛选和分页前返回。
- 库存报表 API 暴露 `build_report` 已有的仓库、SKU、品牌选项。
- 收款报表在授权订单集上按客户快照或现有订单编号搜索；列表、页面和 XLSX 共用该结果。新增独立履约状态字段/列，核对状态保持原字段/列。
- 商品目录 API 支持 `sku_status=active|inactive|all`，显式筛选时产品与嵌套 SKU 一致；缺省查询、共享 `/api/v1/skus` 和下单停用 SKU 校验保持原行为。

应用与回归测试共 7 个文件，提交 `a2073d14a0fdd6026461fb15a28358dfbdf4b2de`（`Add UI list filter API support`）。

### 验证与限制

- 6 项定向回归全部通过，Django system check 无问题。测试运行器创建并销毁独立测试库 `test_aluola_crm_ui_list_filters_20261008`；未使用或删除开发测试库或演示库。
- `git diff --check` 和下列 Python 编译检查通过：

```sh
/Users/chrisz/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m py_compile crm/catalog.py crm/reports.py crm/views.py crm/test_phase3.py crm/test_phase4.py crm/test_phase6.py crm/tests.py
```

- 定向测试命令（使用独立 DB 名、项目 PostgreSQL socket/端口及专用环境）：

```sh
CRM_DB_NAME=aluola_crm_ui_list_filters_20261008 CRM_DB_HOST=/Users/chrisz/Documents/projects/aluolaCRM/.local/postgres-socket CRM_DB_PORT=55439 UV_PROJECT_ENVIRONMENT=/Users/chrisz/.cache/aluola-crm-uiquery1-venv uv run --locked --python /Users/chrisz/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 manage.py test crm.tests.CatalogTestCase.test_product_api_filters_matching_sku_status_without_changing_default_or_order_skus crm.test_phase3.CustomerPhase3Tests.test_customer_owner_filter_and_options_follow_scope_before_search_and_pagination crm.test_phase3.SalesOrderPhase3Tests.test_order_sku_search_and_preview_keep_inactive_sku_restriction crm.test_phase4.OrderListFilterTests.test_order_filter_options_cover_visible_orders_before_filters_and_pagination crm.test_phase6.ReportPhase6Tests.test_inventory_report_keeps_live_balance_and_hoard_reason crm.test_phase6.ReportPhase6Tests.test_payment_report_applies_beijing_boundaries_and_matches_api_page_and_xlsx
```

- 较宽回归使用同一独立 DB 环境，测试选择为 `crm.tests.CatalogTestCase crm.test_phase3.CustomerPhase3Tests crm.test_phase3.SalesOrderPhase3Tests.test_order_sku_search_and_preview_keep_inactive_sku_restriction crm.test_phase4.OrderListFilterTests crm.test_phase6.ReportPhase6Tests`；共 43 项，3 项既有 `/catalog/` HTML 断言失败。它们期待初始 HTML 静态包含“新建订单”“瘦包”及 SKU 文本；固定前端页面改为客户端加载后已不包含这些内容。未改模板或越界调整旧断言；6 项定向新用例通过。
- 不涉及迁移。没有执行浏览器端 UI 检查；当前完成范围为后端 API/报表行为。提交后仅更新本工作记录，随后停止应用写入并交接。

## PAYMENT-REPEAT-AUDIT｜只读核查（2026-10-08）

依据：负责人任务表 `PAYMENT-REPEAT-AUDIT`；只读固定基线 `df63f34325056a9ce06eac0da92c61d216d60f6e`。应用唯一写入者为前端；未改应用代码。可复现的两项顺序合成探针保存在忽略目录 `.local/payment-repeat-audit-20261008/test_payment_repeat_probe.py`。

### 结论与证据

- `POST /api/v1/orders/{id}/payment`、`POST /api/v1/orders/{id}/verify-payment` 分别进入 `register_payment`、`verify_payment`。两者在事务内先锁 `BusinessControl`、再锁授权范围内的订单，检查操作键回放和订单 `expected_version`；核对另锁 `PaymentRecord`。相同键/相同输入返回原响应；同键不同输入返回 `idempotency_conflict`；新键携带旧订单版本返回 `version_conflict`。
- 数据层 `PaymentRecord.order` 是 OneToOne，故一单只有一个当前收款记录；`ReceiptImage.payment` 是多对一，一单允许多张图（业务明确允许），并通过单据互斥约束避免图片同时关联入库和付款。重复提交相同已绑定 image ID 不会再建关联。图片内容没有摘要去重；重新上传同一文件会是新图片ID，当前业务允许多图，不能自行把它当成第二笔付款或禁止。
- 同键重放：原有回归及合成探针通过；不新增 `BusinessOperation`，不增付款/订单版本，不重绑图片。
- 新键＋旧版本：合成探针得到 `version_conflict`；第二张待上传图片保持未关联，操作数和序号不变。
- 新键＋最新版本＋相同收款账户/金额/时间/已绑定图片：服务接受为一条新操作。合成探针观测到 `BusinessOperation(payment_register)`、`SalesOrder.version`、`last_sequence` 各前进；`PaymentRecord` 仍只有一条，金额、`PaymentRecord.version`、首次凭证时间、图片关联数不变。相同输入在已核对通过后重存仍保持通过，但仍增加操作序号。
- 同键核对重放无副作用；新键＋最新版本重复通过核对会新建核对操作并增加 `PaymentRecord.version`、`SalesOrder.version` 和 `last_sequence`，更新核对人/时间；付款金额、首凭证时间、图片关联、`CommissionReview` 金额/版本/首次计入时间及 `CommissionAdjustment` 数量不变。复核提成是独立操作，服务端对同一订单同一金额版本已有 `already_reviewed` 拒绝。
- 当前订单详情表单在付款已通过后仍渲染核对按钮；`runForm` 成功后整页重载，但重载后按钮仍存在。付款登记表单也会重载且继续显示（用于合法更新）。`actionData` 每次生成新 UUID，因此重载后的再次点击是新操作，不是幂等重放；订单详情标题使用会更新的 `last_sequence`，API `id` 则是稳定 `SalesOrder.pk`。所以用户看到的“订单号”变化是把最新操作序号显示成订单编号，数据库订单主键及付款关联没有变化。
- `order_detail.html` 的 `received-at` 输入没有用现有付款资料回填；`paymentImageIds` 会保留已关联图片ID。合成探针验证：已有非空 `received_at` 在重载表单中为空后再次保存会将该可空时间清为 `NULL`，按真实资料变化重置核对状态为待核对并清除已核版本；付款记录和图片仍各自只有原记录/关联。这应由前端回填时间或明确保留字段解决，不是后端重复记账。
- 本轮未运行付款登记并发探针。现有真实 PostgreSQL `test_postgresql_problem_and_ship_race_never_leaves_partial_state`（核对/发货竞态，不是双重登记）通过；负责人已安排 QA 在前端固定交付后独立验证实际重复登记并发，避免重复宽测。
- 审查开始时目标是固定 `df63f34`，当时应用文件无本地改动；随后工作区出现前端对模板/CSS的未提交改动。未读取或触碰这些进行中的改动；本节模板观察仅指固定基线，不代表前端修复后的视觉/行为验收。

### 验证环境

只用合成数据和独立 Django/PostgreSQL 测试库。既有4项定向回归通过（包含收款同键幂等、重新登记状态、首次凭证时间/重核、核对与发货竞态）；一次性顺序探针2项通过。两次测试运行器分别创建并销毁 `test_aluola_crm_payment_repeat_audit_20261008`、`test_aluola_crm_payment_repeat_probe_20261008`；未触碰开发库、用户 demo 或其他角色资产，无服务重启。

```sh
CRM_DB_NAME=aluola_crm_payment_repeat_audit_20261008 CRM_DB_HOST=/Users/chrisz/Documents/projects/aluolaCRM/.local/postgres-socket CRM_DB_PORT=55439 UV_PROJECT_ENVIRONMENT=/Users/chrisz/.cache/aluola-crm-uiquery1-venv uv run --locked --python /Users/chrisz/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 manage.py test crm.test_phase4.PaymentPhase4Tests.test_payment_requires_images_and_reason_for_difference_then_is_idempotent crm.test_phase4.PaymentPhase4Tests.test_account_responsibility_and_pause_survive_re_registration crm.test_phase6.ReportPhase6Tests.test_payment_first_evidence_time_survives_failure_replay_updates_and_rechecks crm.test_phase4.FulfillmentPhase4Tests.test_postgresql_problem_and_ship_race_never_leaves_partial_state
```

```sh
PYTHONPATH=/Users/chrisz/Documents/projects/aluolaCRM/.local/payment-repeat-audit-20261008 CRM_DB_NAME=aluola_crm_payment_repeat_probe_20261008 CRM_DB_HOST=/Users/chrisz/Documents/projects/aluolaCRM/.local/postgres-socket CRM_DB_PORT=55439 UV_PROJECT_ENVIRONMENT=/Users/chrisz/.cache/aluola-crm-uiquery1-venv uv run --locked --python /Users/chrisz/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 manage.py test test_payment_repeat_probe
```

### 最小处理建议（未实施）

当前没有发现会因重复点击生成第二条付款记录、重复金额、重复绑同一图片或重复提成的服务端缺陷；不需要扩展为多笔付款。前端应提交中禁用/反馈、失败重试沿用同一操作键，成功后加载最新状态；通过核对后隐藏或锁定核对动作，并回填当前收款时间。若还希望“刷新后以新键再次提交完全相同内容”不再增加操作序号/版本，可在下一独立服务窗口对同输入当前状态做事务内无变化短路；这会改变当前每次新键都记业务操作的语义，需负责人确认。提成/收款的历史规则未因本审查修改。

## USER-REVISION-1009｜只读后端方案（2026-10-09）

依据：负责人当前任务表及 `docs/project/USER_REVISION_20261009.md`。应用基线 `73dd50c` 仍冻结验收。本工作区现有其他角色未提交改动；本轮没有读取或修改这些应用改动，只更新本角色工作记录，不运行测试。

### 十项对应的后端事实

| 项 | 现状与最小后端范围 |
| --- | --- |
| 客户字段 | `Customer` 当前有 `birthday/interests/hobbies`；客户序列化、创建/修改规范化、客户列表生日筛选、生日页/API、个人提醒偏好都仍在。迁移时 `interests→note1`、`hobbies→note2`，删除生日数据与提醒功能；另加一套可选客户开票资料、订单开票开关及资料快照。客户查看/修改沿用现有权限范围。 |
| 查找 | `GET /api/v1/customers?q=` 只查姓名、公司、联系方式，不查已存别名；`GET /api/v1/skus?q=` 查 SKU/产品字段及两类别名；`GET /api/v1/warehouses?q=` 查名称、地址，仓库没有别名字段。三个列表均分页，订单表单只请求 `page=1&page_size=200` 并忽略 `has_next`，超过200条匹配结果会被截断。后续按获批表单把客户别名加入服务端搜索并保留账号可见范围；前端处理分页/空态。客户/订单动态筛选选项基于账号可见全集、在搜索和分页前生成，现有回归明确验证此行为，未发现选项从当前页错误推导或越权泄漏。 |
| 表单布局 | 控件密度、层级、按钮和首页卡片属于UI/前端；无需改业务API。保留既有工作台指标口径与各数据源权限。 |
| 可售量 | `GET /api/v1/inventory?sku_id=…&warehouse_id=…` 已返回 `available` 和 `unit`；公式为在库减订单占用、客户预留、囤货和破损。订单修改规划会把本单旧明细数量加回可用量，但通用库存GET没有订单上下文。最小接口建议给库存查询加经权限校验的可编辑 `order_id` 上下文，以便展示同一口径；最终预览/提交仍走现有事务锁与库存复核。 |
| 本单开票 | `SalesOrder` 无开票字段。建议增加 `requires_invoice` 与抬头、信用代码、邮箱三个订单快照字段；`Customer` 保存同一套可选资料。勾选开票时服务端要求抬头，其他两项选填；散客模式无客户档案、不开票，服务拒绝 `requires_invoice=true`；只记录资料，不接税务系统。订单使用档案资料或本次新填资料，客户档案同步和订单快照必须在同一提交事务里完成，历史快照不回读客户当前值。 |
| 订单内新客 | 现有 `POST /api/v1/customers` 与订单确认是独立事务；先建客再下单会留下孤立客户。建议在订单预览令牌中包含 `new_customer` 输入，最终 `confirm_sales_order` 的同一 `transaction.atomic()` 内创建客户及收货地址、订单、占货和操作记录；先检查订单操作键回放，失败全回滚，重放返回同一客户ID。不得先调用独立客户POST。客户联系方式与收件人联系方式是不同字段，不假设两者为同一人。 |
| 后台语言 | `LANGUAGE_CODE="zh-hans"` 且 `USE_I18N=True`，已有业务错误和多数字段标签为简体中文，无需加语言依赖；录入的商品名/官方编号是普通文本，不会被翻译。标准Django Admin未配置品牌主题；多个模型没有 `Meta.verbose_name`，语言设置本身不能保证模型标题为中文，需作为UI/本地化检查点。 |
| 商品创建 | 目录页没有写接口；`products_api`/`skus_api` 当前只列目录。`Product.language` 是可空自由文本，`Product.clean` 仅按商品类型要求非空；Django Admin 的 `ProductAdminForm` 使用该字段，SKU表单处理重复条码二次确认。目录新增产品/SKU API沿用 `add_product/add_sku`，不能让销售因入口前移获得新权限；现有默认配置仓管和老板都能维护商品。新增语言选项维护需单独受现有老板/管理员身份约束，不能直接复用仓管也持有的 `add_product` 来放开语言创建。 |
| 期初关闭 | `BusinessControl.opening_completed` 表示已成功导入。增加独立 `opening_enabled` 服务端开关；切换服务必须同时执行既有老板身份检查及 `manage_opening` 授权（超级管理员可绕过），不能只因某普通人员被分配此可授权权限就放行。API 要求 `expected_version` 和操作键；仓管的 `confirm_opening` 不隐含开关管理权。预览/确认检查开关；`opening_completed` 或实际 `OpeningBatch` 后永久拒绝重开/重导入。切换不检查 `InventoryBalance` 是否为空，现有预览/确认库存限制保持原样。 |
| 工作台 | 新版布局及开发注释清理不要求后端数据变更；保持既有汇总/权限合同。 |

### 迁移与事务边界

- 新客户字段按空字符串/空值默认，不加唯一的姓名/联系方式约束，不做跨销售自动合并，不建“档案不完整”字段。快递新客复用订单已有必填收件地址校验并建一个 `CustomerAddress`；客户联系方式仍单独必填。非销售代建沿用现有明确指定有效销售负责人的规则。
- 客户迁移明确为 `interests→note1`、`hobbies→note2`，保留原值后再删除旧列及 `birthday` 数据；删除个人生日提醒字段/API/筛选/页面。`AccountProfile` 仍被 `BusinessOperation.account_profile` 与“恰好一个操作目标”数据库约束引用；删活动偏好字段、API和页面，但保留模型/目标关联及历史操作，不清理旧账本payload。未读取客户行或演示数据。
- 订单新客及发票资料作为预览输入纳入规范化哈希，预览不写库；只在最终确认中写 Customer/CustomerAddress/SalesOrder。散客分支不创建 Customer/CustomerAddress，且服务端拒绝开票。`BusinessOperation` 单条只指一个业务目标，订单创建/客户同步若需分别记账，应在同一事务内写各自目标记录；订单的客户端操作键仍是唯一重放锚点，不能暴露独立客户创建步骤。
- 同一操作键重试可用现有 `_operation_replay` 保证不重复；没有姓名/电话唯一约束，所以网络结果不明后必须重用同一 `operation_key`。新操作键代表新建行为，不能承诺按相似姓名自动去重。
- 业务已确认：客户开票资料一套，档案字段选填；勾选时仅抬头必填；订单快照不随客户更新而改变；线下开票；散客不建档、不需要/不得开票；新客姓名/联系方式必填，快递收件人/电话/省市/详细地址必填，选填空白不标不完整；兴趣/爱好迁入备注1/备注2，删除生日；老板/管理员在入账前可关闭并重开期初，入账后永久禁止重开或重复导入。业务边界已收口，方案仍待负责人技术校准。

### 最小迁移/API/权限方案（供负责人校准）

- **单一迁移**：给 `Customer` 加 `note1/note2` 及 `invoice_title/invoice_credit_code/invoice_email`（空白默认）；给 `SalesOrder` 加 `requires_invoice=False` 与同名三项快照（空白默认）；给 `BusinessControl` 加 `opening_enabled=True`；移除 `Customer.birthday/interests/hobbies` 及 `AccountProfile.birthday_reminder_enabled`。数据迁移先把兴趣/爱好逐行复制到对应备注，再删三旧客户列；已有订单作为 `requires_invoice=False`、空快照；若已有导入记录/库存，开关初始化为关闭。保留 `AccountProfile`、`BusinessOperation.account_profile` FK 与互斥目标约束，避免破坏历史操作关系。
- **商品语言数据**：现状 `Product.language` 是自由文本。最小受控结构为 `ProductLanguageOption(name UNIQUE)` 初始种入“简中、日文、英文、韩文”，`Product.language` 改为指向选项的可空 `PROTECT` 外键、不设默认；补充包/礼盒·预组仍需非空，周边可空。迁移前只读列出旧语言值（按值和类型计数，并列出受影响产品）及必填类型空值；仅精确保留四个已确认值或采用负责人/用户明确批准的映射。非标准值不得猜映射、静默丢弃或自动变成新选项；有未决值时阻断删除旧文本列，先核对后再迁移。当前没有读取任何商品行，不能断言旧值集合。
- **订单 API**：沿用 `POST /api/v1/orders/preview` → `POST /api/v1/orders` 预览令牌/操作键，不建旁路客户创建API。预览规范化输入增加 `new_customer`（档案字段、选填发票资料和现有 `address_snapshot`），提交时验证 `customer_id` 与 `new_customer` 互斥；新客、客户地址、订单行、库存占用、客户/订单操作记录在一次现有确认事务中提交，失败整体回滚。成功后返回同一 `customer_id`；同键重试命中订单回放。散客（无 `customer_id` 且无 `new_customer`）强制不建档并拒绝开票。订单详情和列表仅读订单快照；新客户遵守 `add_customer` 与既有客户归属校验，修改现有客户资料须 `change_customer`，下单继续要求 `add_salesorder`。
- **发票资料**：客户 API 序列化/规范化增客户开票资料。正式客户订单默认复制档案资料；用户在订单中本次填写/修改时，同事务更新客户档案并保存当单快照（更新现有客户须 `change_customer`，新客随 `add_customer` 创建）。始终要求后端校验勾选时抬头非空，信用代码/邮箱可空；发票资料变化不改金额、付款、库存或提成。
- **库存/目录**：在既有 `/api/v1/inventory` 接受可选 `order_id`；仅订单有权且可编辑时把该单当前活动明细占货加回显示可售量。复用一个小的可售量计算函数供库存行与订单规划使用，最终提交仍用锁内复核。产品/SKU创建最小新增写服务/API，使用既有 Admin 表单/模型校验而非重复校验器，分别要求 `add_product`/`add_sku`，保留条码重复确认语义。事务内用操作键重放返回既存 `result`；复用 `BusinessOperation(control=BusinessControl)` 保存操作与新对象ID，不增加产品/SKU外键目标或通用审计框架。销售仍无维护权限。客户 `q` 加入已有别名匹配，继续先做账号权限范围裁剪。
- **商品语言校验/权限/兼容**：Admin产品表单用选项外键下拉；写商品API可接收 `language_id` 并使用现有 `add_product/change_product`，走 `Product.full_clean`/类型校验；给获准录入者的目录只读选项API沿用现有商品查看范围。现有读取API的 `language` 字段继续返回语言显示名；现有 `GET /api/v1/products?language=` 仍以可读语言名做包含筛选，内部改为关联选项名称查询，不能改成ID参数。目录搜索、所有包含语言的序列化/导出显示名称。期初 `_resolve_opening` 将模板中的语言显示名精确解析为既有ID；模板标题及员工填写仍是可读语言名，不要求数字ID；未知值定位到行/语言字段并拒绝预览，不在导入时创建选项。所有写入路径使用同一个 `Product.full_clean`/类型校验及数据库非空约束，必填类型缺值拒绝、周边空值保留、无默认值。语言选项Admin维护只允许现有老板角色/管理员身份（超级管理员），不依赖仓管可拥有的 `add_product`，不添加独立权限组或普通角色开关；仓管录产品只能选既有语言。SKU创建继续复用 `add_sku/change_sku` 及原重复条码确认。
- **期初控制**：新增 `manage_opening`，但服务仍须同时确认既有老板身份；该权限自身不能让普通人员取得控制权，超级管理员按现有方式绕过。`PATCH /api/v1/opening/control` 要求 `{enabled, expected_version, operation_key}`，沿用 `_lock_control`、`_operation_replay`、`last_sequence` 版本冲突检查及 `BusinessOperation(control=...)`。切换只依据 `opening_completed` 或已有实际 `OpeningBatch` 阻止重开/再次启用，不把 `InventoryBalance` 为空作为关闭或重开门槛；保留预览/确认现有库存拒绝逻辑。预览与确认都拒绝 `opening_enabled=False`；成功入账原子地置 `opening_completed=True, opening_enabled=False`。切换和导入共用 control 锁，锁内复核状态，防止并发穿透。

### 后续验收要点（未执行）

迁移逐行保留兴趣/爱好到新备注且删除生日；语言迁移只精确匹配已确认值，先列出旧值/必填类型空值，未决非标准值不映射、不丢弃并阻止旧列删除；四个初始选项、无默认，补充包和礼盒必选、周边可空；Admin/API/期初导入只允许既有选项且共用 Product 类型校验，导入未知值带表行定位并拒绝；读取API `language`、现有 `language` 包含筛选、查询/导出展示和期初模板均保留可读规范名称，只有新增写接口可传选项ID；仓管可创建商品但不能新增语言，老板/管理员可维护选项，不引入独立权限体系。生日路由/参数/提醒偏好均移除；散客提交开票被服务端拒绝且不造客户；开票快照在客户后续修改后不变；订单新客失败整体回滚、同键重放不重复；非销售客户归属受权限约束；编辑订单可售量加回本单现有占货、不可编辑订单不能伪造上下文；目录写权限、重复条码确认和同键重放返回同一对象ID；期初未入账时老板身份可关闭/重开、单独授予 manage_opening 的普通人员仍拒绝、缺失/过期 expected_version 冲突；有采购库存时仍可关闭开关、但原预览/确认库存限制不变；已有 OpeningBatch/成功导入后不能重开；关开与导入并发被串行化；页面简体中文。当前仅核对源码/文档，未做浏览器搜索复现、HTTP请求、数据探针或测试；这些由前端/QA按获批候选及后续实现分别验收。

## USER-REVISION-BACKEND-1009｜实现与后端自测（2026-10-09）

依据：负责人当前任务表、用户已批准的 `revision-v1` 以及当前 `docs/specs/API_CONTRACT.md` 增量合同。应用基线为 `d9d2939`；本轮后端是唯一应用写入者，固定后停止应用写入并交独立QA。没有修改负责人/其他角色文档或原型。

### 实现范围

- 新增单一迁移：客户兴趣/爱好原值分别改存备注1/备注2，删除生日与提醒字段；加入客户/订单开票资料、订单开票快照、受控商品语言及期初开关。标准语言精确映射；未知旧语言按语言和类型列出计数及最多50个产品ID后抛错，迁移事务回滚，旧字段/行保留，不自动猜映射。若存在 `OpeningBatch` 或已完成入账，开关初始化为关闭。
- 删除生日页面、URL、API、筛选和提醒偏好；客户 `q` 增加现存别名匹配；客户 API/表单使用备注与开票资料字段。
- 订单确认事务内创建新客户及首条快递地址，客户/收件人信息分开验证，支持开票资料入档与当单快照。预览绑定全部输入；确认失败回滚客户、地址、订单、占货和操作；同键重放返回原结果。现有客户仅在开票资料实际变化时要求 `change_customer`；散客开票拒绝。
- 增加库存查询 `order_id` 上下文和 `available_for_order`，保留原 `available` 值；新增受权限约束的商品语言列表、商品+SKU创建、SKU创建、编号建议、当前产品页可选库存扩展，以及带老板身份检查和版本/幂等检查的期初管理开关。
- 本轮回归揭出并修复新分支中的两处问题：新客订单规范化调用没有传入操作者；SKU/产品行锁查询包含可空语言外连接，PostgreSQL拒绝执行 `FOR UPDATE`。查询现只锁所需主表行，目标回归通过。

### 验证与限制

- Django系统检查通过，`makemigrations --check --dry-run` 显示无模型状态差异；`compileall` 与 `git diff --check` 通过。
- 使用本机PostgreSQL socket/55439及本轮独立数据库 `aluola_crm_revision_backend_20261009`，运行48项针对性测试全部通过。包含迁移成功/未知值中止并保留旧数据、生日移除、客户API/订单开票、新客原子回滚与幂等、订单可售量与范围、目录商品/SKU创建和重复条码确认、期初权限/开关，以及既有真实PostgreSQL双连接防超卖回归。Django测试运行器创建并删除 `test_aluola_crm_revision_backend_20261009`。
- 只用了合成数据；没有迁移、读取或更改开发库、演示库、正式数据、服务状态或远端。没有运行全量测试集、浏览器/前端完整链路、或期初开关与入账的双连接竞态；这些留给独立QA按交接矩阵验证。当前不代表UI验收或负责人最终发布。

### 固定交付

应用固定提交：`e1f81ebd3b26bf79899a1e1f852bdbc19364dd92`。该提交包含本次应用变更及本工作记录；提交后已停止应用写入。后续仅把本段固定版本记录作为文档提交，不更改应用文件。

48项定向测试命令：

```sh
UV_PROJECT_ENVIRONMENT=/Users/chrisz/.cache/aluola-crm-uiquery1-venv CRM_DB_NAME=aluola_crm_revision_backend_20261009 CRM_DB_HOST=/Users/chrisz/Documents/projects/aluolaCRM/.local/postgres-socket CRM_DB_PORT=55439 uv run --locked --python /Users/chrisz/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -- python3 manage.py test crm.test_user_revision crm.tests.CatalogTestCase crm.test_phase2.OpeningPhase2Tests crm.test_phase2.OpeningControlPhase2Tests crm.test_phase3.CustomerPhase3Tests crm.test_phase3.SalesOrderPhase3Tests --verbosity=1
```

隔离测试基库 `aluola_crm_revision_backend_20261009` 已删除；测试运行器创建的 `test_aluola_crm_revision_backend_20261009` 已自动删除。下一步由QA以应用固定提交SHA为基线，在自建隔离PostgreSQL库独立验证非标准旧语言迁移回滚、完整客户/订单事务和期初竞态；不连接或复制用户/演示数据。负责人和QA交接后等待独立测试结果。

## USER-REVISION-INVOICE-REPLAY-AUTH｜只读缺陷定位（2026-10-09）

依据：QA对固定应用提交 `e1f81ebd3b26bf79899a1e1f852bdbc19364dd92` 的独立测试反馈；负责人要求只读定位，待QA完整报告后统一开放修复窗口。本次没有恢复应用写入，也没有运行测试。

### 根因与最小修复建议

- `crm/sales.py` 的 `confirm_sales_order` 在 `_operation_replay` 命中后直接返回结果，未复核原订单操作中嵌套的客户资料修改权限。
- 首次确认若实际修改已有客户开票资料，同一事务已写入 `BusinessOperation(action="customer_order_invoice_update")`；其 `payload` 保存 `order_operation_key`、`customer_id`、`changed_fields`，并通过 `customer` 外键关联客户。若字段没有实际变化，则不会创建该嵌套操作。
- 返工只需在 `confirm_sales_order` 的原样重放分支、返回前查找 `action="customer_order_invoice_update"` 且 `payload.order_operation_key=str(key)` 的记录。仅找到该记录时，重新要求当前 `change_customer` 并通过 `customer_queryset_for(actor, changing=True)` 验证客户当前维护范围；权限不足返回403，已超出范围按现有客户维护路径返回404。检查通过后返回原结果。
- 不改通用 `_operation_replay`，不按订单上的 `customer_id` 一概要求客户权限；未更新客户资料的既有客户订单及新客创建不额外要求 `change_customer`，不新增表或迁移。

### 建议回归边界

1. 已有客户开票资料确实被订单更新后撤销 `change_customer`，使用新会话以同一操作键和预览令牌重放：返回403，客户字段/版本、订单和操作记录均无变化。
2. 客户开票资料本来与订单输入一致，没有嵌套更新操作；撤销 `change_customer` 后原样重放仍成功。
3. 保留 `change_customer`，但将客户转出操作者当前维护范围；原样重放返回404。

建议在 `crm/test_phase3.py` 现有客户开票/订单确认测试附近覆盖。上述为收到正式修复窗口前的只读状态；授权实施与复测结果见下节。

### 授权修复与固定交付（2026-10-09）

负责人正式开放修复窗口后，仅修改 `crm/sales.py` 与 `crm/test_phase3.py`。应用固定提交：`694aaad08dfaa12b92bf3e18360edf686b588f92`；提交后已停止应用写入。

- 重放命中时，仅按当前 `operation_key` 查找关联 `customer_order_invoice_update`。找到记录才重新检查 `change_customer` 及 `customer_queryset_for(actor, changing=True)`；权限撤销返回403，客户已转出维护范围返回404。没有该嵌套操作记录的订单仍走原重放路径。
- 三条新增回归在修复前先运行：撤权和转出范围两例都错误返回200，无实际客户更新的控制例返回200。修复后同三例全部通过。
- 使用隔离 PostgreSQL 基库 `aluola_crm_invoice_replay_auth_20261009` 运行客户/订单 Phase 3 类共27项测试，全部通过；包含三条新增边界与 PostgreSQL 双连接防超卖回归。测试运行器自动清理 `test_aluola_crm_invoice_replay_auth_20261009`，基库随后已删除。
- `compileall`、目标文件 `git diff --check`、`makemigrations --check --dry-run` 均通过；后者显示 `No changes detected`，本修复无模型迁移。

负责人及QA已收到固定应用SHA和测试证据；等待QA按固定提交独立确认。本轮不推送、不部署，也未触碰其他角色文档、期初模板、用户库或服务。

## UI-DASHBOARD-STATS｜只读数据接入方案（2026-10-09）

依据：用户在 `docs/project/USER_REVISION_20261009.md` 的直接答复；开发计划当前指定前端为唯一应用写入者。此项只读核对，不改应用或公共API合同，不运行测试。

### 已确认口径与待决项

- 客户排行按本月已交付金额（快递按发货、现场按完成日期）；销售额增速比较北京时间今天与昨天全天，昨天为零时显示“—”。
- 在售IP＋语言组合不含零库存；空语言周边仍按该IP的一组计，不按SKU拆分。销售日期继续按快递发货／现场交付时间，时区为 `Asia/Shanghai`，客户排行仅使用原授权订单范围。
- 客户排行仅含有 `customer_id` 的档案客户，不读客户档案权限外的数据；名称复用授权订单名称快照。同一客户有多个名称时，取本期最近交付订单快照，以交付时间降序、订单ID降序稳定排序。原型显示Top3，金额3位。
- 增速用高精度 `Decimal` 计算后四舍五入至2位；昨日金额为0时返回 `null`，页面显示“—”。
- 尚未确认“零库存”以 `on_hand`（在库）还是 `available`（可售）为准。组合卡依赖此判断，当前字段应省略或为 `null`，页面显示“—”，不能推定为0。

### 已有逻辑及最小返回形状建议

1. **客户本月排行**：沿用 `/api/v1/reports/sales` → `reports_api` → `build_report` → `_sales_data`。请求 `workbench=customer_ranking`、`scope=delivered`，并用自定义北京时间本月1日至今天筛选；不要用 `period=1m`，它是滚动一个月。订单范围由 `_order_queryset`／`sales_order_queryset_for` 维持具备 `view_all_sales_orders` 的用户查看全部、其他用户查看本人订单，取消订单和非活动明细沿用现有排除规则；不查客户表，不扩展读取权限。建议响应 `summary.workbench.customer_ranking: [{rank, customer_id, customer, revenue_amount}]`，只含 `customer_id` 非空客户，按金额降序、`customer_id` 升序稳定取Top3；`customer` 从本期授权订单快照取，按有效交付时间降序、订单ID降序决胜。金额复用 `_money` 的3位字符串。可在 `_sales_data` 现有明细遍历中按客户 `Decimal` 累计并跟踪最近快照，避免浏览器下载或聚合全量报表行。
2. **今日对昨日增速**：请求 `workbench=day_over_day`、`scope=delivered`、`series=day`，自定义日期只取北京时间昨天和今天。已有 `_sales_date_query` 按发货／现场完成时间筛选；`_sales_data` 已生成含零日的顶层 `series: [{date, revenue_amount}]`，且两日范围在现有366日限制内。建议服务端在同一遍历中返回 `summary.workbench.day_over_day: {today_date, today_revenue_amount, yesterday_date, yesterday_revenue_amount, growth_percent}`；金额复用3位字符串，增速为 `(today / yesterday - 1) × 100`，用高精度 `Decimal` 后以 `ROUND_HALF_UP` 四舍五入到2位并返回字符串，昨日金额为零时返回 `null`。月排行和两日增速使用两次同源报表请求，这样月初也包含上月最后一天，且不把业务计算移到浏览器。
3. **在售IP＋语言组合数**：沿用 `/api/v1/reports/inventory`、`inventory_rows` 与 `_inventory_data` 的当前余额和原 `view_inventorybalance` 权限。待库存谓词获确认后，请求 `workbench=ip_language_stock_count`；建议响应 `summary.workbench.in_stock_ip_language_combination_count: <整数>`。服务端在授权余额全集按 `(brand_id, language_id)` 去重，一个组合只要任一仓库／SKU余额满足已批准的正库存条件就计1，不累加不同单位，不造缺失余额行。空语言即 `(brand_id, null)`；同一IP下所有空语言SKU只算一组。谓词确认前不请求此指标、不返回数值。

### 原报表兼容性与消费者使用

- `_sales_data` 现有行按SKU×仓库×销售×客户×渠道×省市分组，`summary.totals` 是全销售报表汇总，二者都不能直接当客户排行。可复用相同授权过滤及明细遍历，增加并行的客户累计/最近名称快照；无需重建报表或扫客户表。`series=day` 已可提供两日金额，增速仅需服务器端补充摘要计算。
- `_inventory_data.summary.by_unit` 是不同售卖单位的当前数量汇总，不能给出不同单位无关的IP×语言组合数；当前余额行带有SKU到商品/IP及语言关联，可在同次库存处理内维护一组 `(brand_id, language_id)`。待定的唯一业务判断仍是正库存采用 `on_hand > 0` 还是 `available > 0`。
- 工作台消费者只读 `summary.workbench`，不聚合 `items`。建议销售排行请求 `workbench=customer_ranking&period=custom&start_date=本月首日&end_date=北京时间今天&scope=delivered&page_size=1`；增速请求 `workbench=day_over_day&period=custom&start_date=北京时间昨天&end_date=北京时间今天&scope=delivered&series=day&page_size=1`。库存谓词确认后再请求 `/api/v1/reports/inventory?workbench=ip_language_stock_count&page_size=1`。日期由消费者按 `Asia/Shanghai` 构造；`page_size=1` 只缩小返回的报表行，不改变完整查询摘要。
- `workbench` 应仅接受各报表支持的显式值，未知或跨报表值沿用现有400错误格式。新增摘要只在对应opt-in时返回，原报表默认 `items`、分页、权限及响应保持不变；销售仍需 `view_salesorder`，库存仍需 `view_inventorybalance`，没有新工作台/客户档案读取权限。消费者无对应权限时沿用无权数据块不渲染行为。

### 范围与交接

- 不新建聚合端点、模型／统计表或前端聚合；上文 `workbench` 值及 `summary.workbench` 字段是给负责人冻结的接口提案，不代表已实施合同。公共合同 `docs/specs/API_CONTRACT.md` 第202、208行仍有旧的统计未定占位，由负责人冻结时同步更新并补入零库存谓词。
- 代码依据：`crm/views.py:775-805`（报表权限／响应）、`crm/reports.py:79-123`（日期与北京时间）、`:131-132`（订单范围）、`:208-258`（库存摘要）、`:261-270`（交付日期）、`:273-369`（销售金额与日报表序列）、`:589-626`（报表路由）、`crm/sales.py:54-59`（本人／全部订单范围）、`crm/operations.py:1151-1152`、`:1155-1229`（可售量与库存余额行）。这些位置已只读核对。
- 后端不改应用文件、不测试；负责人在前端写入窗口结束后冻结查询参数、响应字段及零库存口径，再安排单一应用写入者实施。

## UI-DASHBOARD-STATS｜后端固定交付（2026-10-10）

依据：负责人派发与 `docs/specs/API_CONTRACT.md`“工作台统计接入合同（2026-10-09最终收口）”。该最终合同替代上方早期“统计口径待收口”的建议。应用基线 `37bd181`、业务基线 `c40d8aab28e5b3a94a35b18e9cb0a8e5cbb62cc7`；本轮后端为唯一应用写入者。

### 实现

- 在现有销售报表增加 `customer_ranking` 和 `day_over_day` opt-in 摘要；客户排行只读取订单客户ID及名称快照、排除散客、按最新本期交付订单快照命名并返回Top 3。增速以Decimal计算并按合同舍入；新增指标截止当前时刻，原报表items、summary、Excel数据和逐日series保持现有口径。
- 在现有库存报表增加活跃IP/商品/SKU且全仓可售合计大于0的IP×语言组合数；保持库存权限和原查询范围。
- 拒绝未知或空 `workbench`、跨报表标记及与指标不符的范围、日期和筛选。未新增路由、模型、迁移或日志；默认API响应无新增摘要。

### 验证

- 使用本机PostgreSQL socket/55439和专用合成空库 `aluola_crm_workbench_20261009`，6项定向测试通过：新增4项统计API回归及既有销售分组、实时库存报表回归。覆盖权限/范围、散客排除、快照名、北京时间跨日、当前时刻边界、Decimal舍入、昨日金额为零、组合去重/停用及锁至零、分页独立汇总、默认响应和Excel行兼容、未知筛选拒绝。
- `manage.py check`、`makemigrations --check --dry-run`、`py_compile`、`git diff --check` 均通过；无迁移差异。
- Django测试运行器已删除 `test_aluola_crm_workbench_20261009`；已复查并删除本轮唯一创建的基库。未连接、读取或更改演示库/现存数据库，未启动或停止服务。

### 固定与交接

- 应用固定提交：`5f04dfc9cd074c314197c84383dfc04d2bf39adb`。提交后已停止应用文件写入；本次补记仅改本角色WORK。
- 接下来将固定版本交负责人和QA；QA须等负责人确认该固定基线后再独立开测。此交付不包含前端接入、浏览器完整链路、部署或上线。

## UI-DASHBOARD-STATS-PREVIEW｜只读依赖评估（2026-10-10）

依据：负责人直接委托的获批UI数据缺口评估；审批依据为 `docs/workspaces/ui/revision-v1/HANDOFF.md` 第27行。当前QA应用基线 `0c6b43a`，工作区HEAD `e69a6a2`；核对 `crm` 在该基线后无差异。本次只读，无应用/API合同修改，也未运行测试。

### 字段与口径建议

- 在 `summary.workbench` 增加 `in_stock_ip_language_combinations_preview` 数组，最多3项；每项仅 `brand_id`、`brand_name`、`language_id`、`language_name`。ID沿用商品目录API的整数形式，`language_id` 可为 `null`；空语言的 `language_name` 固定显示“未标语言”。
- 预览与现有 `in_stock_ip_language_combination_count` 使用同一个合格组合集合：仅启用IP/商品/SKU，且该SKU跨所有仓库的可售量合计大于0；每个 `(brand_id, language_id)` 去重一次。count继续返回全量；preview按 `brand_id`、`language_id` 升序取前三项。空语言排序建议明确为 `language_id` 的NULL优先，以便混合空值和整数时仍有确定顺序。
- 组合集合为空时preview为 `[]`、count为0。前端只渲染服务端给出的preview，不从分页items拼接或补全。

### 可复用点、权限与兼容

- `_inventory_data` 已有 `available_by_sku` 和 `sku_groups`；当前count正是从所有仓位的可售量累加后，筛出正数SKU并按 `(brand_id, language_id)` 去重。新增预览可直接投影该集合，不引入第二套判断。已有余额对象预取了IP名称；语言名称目前未 `select_related`，若实施只需给现有余额查询补 `sku__product__language`，避免逐行读取语言产生N+1。
- 没有现成的IP×语言组合预览。`products_api(include_inventory=true)` 虽返回商品的IP/语言及当前页SKU库存，但按商品分页、粒度不同，不能代表所有满足条件的去重组合；不适合作为预览源。`product_data` 的ID为整数，可作为字段类型约定参照。
- 请求仍使用 `/api/v1/reports/inventory?workbench=ip_language_stock_count&page_size=1`。现有报表和 `inventory_rows` 均要求 `view_inventorybalance`；workbench拒绝仓库、IP、SKU、低库存和订单上下文筛选，保持全量当前余额范围。分页只影响items，不影响摘要。默认响应不加preview，Excel只消费items/columns，摘要不影响导出。

### 最小回归建议

扩展 `test_inventory_workbench_counts_enabled_positive_available_ip_language_pairs`：现有夹具已有4个合格组合，断言count仍为4、preview恰为3项、字段集合严格等于上述4字段且按ID排序；至少包含同组合跨仓/多SKU去重及空语言标签。已有测试已覆盖全仓可售为零、停用IP/商品/SKU排除、`page_size=1` 全量摘要、默认响应不含workbench及无权限403；保留这些断言，并对default/opt-in导出的工作表行做相等比较即可验证Excel兼容。不需新端点、参数、模型、权限或统计规则。

下一步由负责人决定QA完成后的单写入者窗口；本轮只提供依赖评估，应用保持冻结。

## USER-REVISION-STOCK-PREVIEW｜实现与固定交付（2026-10-10）

依据：负责人正式开放的最小后端写入窗口及 `docs/specs/API_CONTRACT.md` 最后“获批商品卡组合预览”条款。起始QA应用基线 `0c6b43a`；本轮唯一应用写入者。只改 `crm/reports.py`、`crm/test_phase6.py` 和本角色WORK；未改前端、Admin、公共合同、用户模板或其他角色文件。

### 实现

- `summary.workbench.in_stock_ip_language_combinations_preview` 从生成全量 count 的同一 `(brand_id, language_id)` 正可售组合集合中按 `brand_id` 升序、`language_id` 升序且NULL优先取前三项；每项只有 `brand_id`、`brand_name`、`language_id`、`language_name`，ID为整数，空语言ID为null、名称为“未标语言”。count仍为全集数量。
- 对既有余额查询增加商品语言 `select_related`，名称读取不产生逐行查询。没有增加端点、参数、模型、迁移、日志或权限；默认响应、items/分页、Excel列和行维持原口径。

### 自测与资源清理

- 专用合成PostgreSQL基库 `aluola_crm_stock_preview_20261010` 上运行2项测试通过：`test_inventory_workbench_counts_enabled_positive_available_ip_language_pairs`、`test_inventory_report_keeps_live_balance_and_hoard_reason`。新回归验证count为4而预览仅3项、字段精确、ID排序/NULL优先及空语言名；保留跨仓/多SKU去重、零可售、停用实体、page_size=1全量摘要、默认响应和403覆盖，并比较default/opt-in库存Excel表格行。
- `manage.py check`、`makemigrations --check --dry-run`、`py_compile`、`git diff --check` 均通过；无模型迁移差异。测试运行器已清理 `test_aluola_crm_stock_preview_20261010`；已确认并删除本轮创建的基库。未连接或更改现存/演示库，也未启动或停止服务。

### 固定与交接

- 应用固定提交：`a1ae3043d83110dead4a575be0b27d1f5c77f550`。提交后已停止应用写入；本次补记只改本角色WORK。
- 发送负责人和QA后，QA待负责人确认新固定基线再做定向独立验证。此为后端自测，不代表QA/UI验收。

## UI-HOME-REFINE｜客户卡今日新增数据依赖（只读，2026-10-10）

依据：负责人委托的获批局部候选数据依赖核对。稳定应用为 `0f835cb`；当前HEAD `b484a2c` 相对该版的 `crm` 无差异。仅检查接口/模型/消费者和相关业务约定，不改源码、公共合同或数据库，未运行测试。

### 现状

- `GET /api/v1/customers` 需 `view_customer`。`customer_queryset_for` 对superuser/`view_all_customers`用户返回全部客户，其他用户只返回 `owner_id=当前用户`。`include_archived` 默认为false并过滤 `is_active=True`；可选 `owner_id`、`q` 在分页前继续收窄同一查询集。
- `paginate` 对过滤后的查询集计算完整 `total` 后再按 `page/page_size` 切items，因此当前工作台 `/api/v1/customers?page_size=1` 可直接取得可见客户总数，不会只数当前页。
- `Customer.created_at` 是 `auto_now_add` 时间戳，但 `_customer_data` 不返回它，customers列表响应也没有今日新增字段或日期筛选。当前工作台只读 `data.total`，新增数不能从现有响应或当前页可靠取得。
- 配置为 `TIME_ZONE="Asia/Shanghai"`、`USE_TZ=True`；customers响应的 `queried_at` 已使用 `timezone.localtime()`。当前没有单独的客户创建人字段，客户归属字段是可转交的 `owner`。

### 最小服务端方案

在现有GET增加 opt-in 参数 `include_created_today=true|false`，只在true时追加顶层整数 `created_today_total`；原有 `total/items/pagination` 不变，默认请求不增加字段或count查询。工作台在同一请求发送 `?page_size=1&include_created_today=true`，消费既有 `total` 和新字段，不读更多页、不读取或计数客户资料列表。

计算复用完成权限校验、归档默认状态、`owner_id`与`q`筛选后的同一 `items` QuerySet，并在 `paginate` 前执行：以一次捕获的 `now=timezone.localtime()` 计算北京时间当天零点，过滤 `created_at >= 当天零点 AND created_at <= now` 后在数据库 `count()`。这样日期上界是当前时刻，分页不影响新增数；不必把 `created_at` 加到每个客户item。

### 范围与业务语义

- 按要求沿用客户总数的权限、当前归属范围、查询筛选与归档状态：默认只数可见的有效客户；显式 `include_archived=true` 时，今日新增也包含同一查询集中的归档客户。`owner_id`/`q` 也同时约束新增数。
- 转交会按新owner改变普通用户当前可见客户与今日新增数，行为与现有total一致；没有 `created_by`，因此该数字表示“当前授权范围内今天创建且符合状态筛选的客户”，不是“由当前用户亲手创建”。用户要求与客户总数共用范围后，归档/转交没有额外待定口径；只有若将来要求按创建人统计，才需要另行业务规则/数据字段评估。
- 不新增端点、表、权限或增长率，不在前端聚合/拉全库；当前工作台已有 `view_customer` 条件渲染，沿用同一API权限即可。业务支持“归档不删历史客户”“转交影响后续客户查看范围”，与复用当前列表查询集一致。

下一步等负责人批准接口方案及排定写入者；当前仍只读支持，应用保持冻结。

## ASTRA-BACKEND-FIX-1013｜最小后端修复与自测（2026-10-13）

依据：负责人开放的唯一后端写入窗口 ASTRA-BACKEND-FIX-1013；起始 HEAD `630d50c`，应用基线 `18dad2d`。仅修改本轮涉及的 `crm/` 源码、回归测试和本角色 WORK；保留用户模板及 QA/UI/审计工作区的现有改动。

### 修复

- 成本纠错只在纠错前提成审批版本仍与订单金额版本一致时，自动同步金额并生成成本纠错调整；已过期的审批金额、版本和审查人保持原样，正常复审计算差额。
- 盘点重提将 `submitted_by` 更新为实际重提人；既有操作流水保留原提交人和重提人，原有负责人自审例外不变。
- 收款与采购入库绑定都锁定 `ReceiptImage` 行并验证实际绑定数；过期图片清理也锁同一行，事务内删数据库行、提交后删文件。
- 订单修改在幂等回放前重新校验当前订单维护范围。开票回退到客户档案值时校验客户当前可见范围；签名预览保留回退来源，确认时再次校验。历史发票快照随无关修改保留，仅主动改变且不同于历史快照的字段写回客户档案。
- 盘点方向从正盘盈翻转为负盘亏时按历史时点平均成本计价，保留手工模式和原手工输入；商品与SKU目录查询预载语言关系。

### 自测与数据边界

- 14 项定向 Django 测试全部通过，包含 PostgreSQL 双连接的“绑定先行”和“清理先行”竞态、收款/采购绑定回滚、权限撤回与客户转交、历史开票快照、提成当前/过期审查、盘点重提与方向翻转、目录语言查询数。
- 使用新建前确认不存在的隔离基库 `aluola_crm_astra_fix_20261013`；实际测试库为 `test_aluola_crm_astra_fix_20261013`，所有者 `crm_dev`。测试结束后 Django 已删除测试库；复查基库和测试库均不存在且活动连接为0。PostgreSQL socket 服务保持运行。
- 所有修改文件 `py_compile` 与 `git diff --check` 通过；Django 系统检查通过。未增加模型、迁移、日志或依赖。
- 未连接或改动默认开发库、演示库、云库、8765 服务、用户期初模板或其他角色文件；未部署。
- 应用固定提交：`956be9c15c931b7bcd9fe9d8684e9894603de2b4`；之后停止应用源码写入。

### 交接

应用提交固定后停止应用写入；等待 QA 对固定版独立定向验证及负责人验收。该自测不代替独立 QA/UI 验收。

## ASTRA-A6-TARGET-CUSTOMER-FIX｜目标客户回退身份修正（2026-10-13）

依据：负责人授予的唯一最小修复窗口；应用基线 `956be9c15c931b7bcd9fe9d8684e9894603de2b4`。只改发票回退标记的客户ID来源和对应Phase3回归，其他批修、UI与数据均未改。

- `_invoice_fallback_customer_id` 现在返回请求中的 `payload.customer_id`，即 `_order_references` 实际读取并用于发票字段回退的目标客户；确认和幂等重放按同一个目标客户检查当前可见范围。
- 新增覆盖：旧客户已转交、当前销售切换到可见新客户并省略发票字段时，预览/确认/可见范围内重放成功；新客户转交后，确认和既有成功操作的重放均返回当前范围拒绝。保留旧客户转交后不能回退旧资料、历史快照只改数量不写回客户档案的既有用例。
- 在隔离基库 `aluola_crm_a6_customer_source_20261013`（角色 `crm_dev`）运行4项A6定向测试，通过；测试库由Django删除，复查基库与测试库均不存在、活动连接为0。语法编译、`git diff --check`及Django系统检查通过；未使用`--keepdb`，无模型/迁移变更。
- 补正应用提交：`8a52ee7cbe57ebd0665bc48dd39f8a5574e0f206`；自此停止应用写入。
- 本次修复固定提交后停止应用写入，交 QA 按固定SHA独立复验。后端自测不代替QA验收。

## UI-HOME-REFINE-CUSTOMER-COUNT｜后端固定交付（2026-10-12）

依据：负责人正式开放的唯一后端写入窗口及 `docs/specs/API_CONTRACT.md`“UI-HOME-REFINE今日新增客户”。起始应用基线 `0f835cb`；起始HEAD `0d5eed8` 后仅开发计划与部署WORK文档更新，`crm`无差异；本轮只写入 `crm/views.py` 与 `crm/test_phase3.py`。

### 实现与自测

- `GET /api/v1/customers` 严格接受 `include_created_today=true|false`；仅true时，在同一最终 `items` 查询集（权限、默认归档状态、`owner_id`、`q`）上，以一次捕获的北京时间计算当天零点至当前时刻并在分页前计数，追加顶层整数 `created_today_total`。默认及false响应不增加字段；未新增端点、模型、迁移、权限或日志。
- 固定应用提交：`ac2f248dce068e88c203e9f0ffdd50030a68a786`。此提交后已停止应用文件写入。
- 在新建且此前不存在的独立测试库 `test_aluola_crm_today_customer_20261012` 上，定向运行3项客户API测试，通过；覆盖当天零点、当前时刻、昨天及未来时间、普通销售owner范围、显式owner与q、归档计数、`page_size=1`、默认/false兼容、非法布尔400和无权限403。Django测试系统检查通过。
- Django测试运行器在结束时删除了该测试库；之后查询确认测试库与配套基库均不存在。未连接或改动demo库、旧开发库、云端数据库；未使用`keepdb`，未启动或停止服务。`git diff --check`通过；无模型或迁移变更。

### 交接

已固定后端应用版本并停止应用写入。下一步将固定SHA与自测证据同步负责人及测试；测试等待负责人确认该固定版后再独立开测。前端不与本轮并行写应用。

## LOGIN-HOME-1013｜默认登录进入工作台（2026-10-13）

依据：负责人正式开放的唯一应用写入窗口。起始应用基线 `c29ca9a`；起始HEAD `991f191` 相对该版仅有文档变化，应用目录无差异。只改 `config/settings.py` 与 `crm/tests.py`，未改路由、账号、权限或运行发布副本。

### 实现与自测

- 将 `LOGIN_REDIRECT_URL` 从 `/catalog/` 改为现有工作台 `/`。Django 登录视图的安全 `next` 逻辑保持原样。
- 登录回归通过真实 `/accounts/login/` GET/POST、CSRF token 和session验证：无 `next` 跳到工作台，合法 `/catalog/` `next` 仍保留；匿名请求工作台仍被重定向。原CSRF登录及目录/API匿名/权限测试也通过。
- 固定应用提交：`0399e4f6cb1e572e3b15628a1c5d036bb043911e`；提交后已停止应用写入。
- 新建前确认不存在的隔离测试库 `test_crm_login_home_20261013_02` 上运行3项定向测试通过，Django系统检查通过；测试运行器已删除测试库，复查专用基库与测试库均不存在。未使用`keepdb`、未触碰默认开发库/演示库/云库；无模型或迁移变化，`git diff --check`通过。
- 同轮额外试跑既有工作台页面用例时，匿名访问断言通过，但随后因测试仍查找“今日已交付销售额”、当前获批模板显示“今日销售额”而失败。与本次登录跳转无关，未修改页面或该文案断言。

### 交接

固定版本待测试角色独立定向QA并由负责人验收；QA期间应用冻结。没有部署或更新运行副本。

## BUSINESS-DATA-REBUILD-1019｜后端只读技术收口（2026-10-19）

依据：负责人当前任务表、获批的数据重建范围、用户对期初成本/普通销售分类的最新答复、只读预检 `.local/initial_data/readonly_precheck_20261019.md`，以及当前应用 71cf9f6 / schema 0015。仅检查代码图、规格及脱敏汇总；未读客户原始值，未改应用、数据库、服务或测试资源；仅写本角色 WORK。

### 当前代码能承载的部分

- 现有 `confirm_opening` 仅在期初入口开启、`opening_completed=False` 且余额表为空时接受期初；只能在后续获准的隔离候选库使用，不能改现库完成标记或重复确认。用户已确认期初流水“单价”是单位成本，故候选的108条期初成本可按此映射，商品参考成本只用于核对。
- 现有采购确认允许 `image_ids=[]`；Supplier只有必填且唯一的名称字段，因此来源中已有的两种供应商名称足以作为候选主档映射，不需编造联系人等资料。所有采购凭证空白本身不阻断入库记录。
- 用户已确认15条原本空白的出库“业务类型”按普通销售处理；这解决了分类本身，但不补出缺失的销售渠道、交付方式或付款凭证。
- 对19条缺出库成本的流水，若期初、采购成本、实际限制数量和事件顺序完整，可用现有仓库-SKU移动平均回放派生；不可补零。对已有来源成本也应与回放结果并列检查，不把导入包装成财务对账或提成审批。

### 会阻断完整、忠实历史入账的缺口

- 用户已确认11组成对记录表示向外部合作方采购后再做普通销售；按采购与销售两件正常业务分别还原，不净额抵消，也不改标为内部调拨。
- 现行普通销售API要求销售渠道、交付方式（快递/现场）和正向数量；出库确认还要求已登记至少一张付款图片，快递另需承运方及物流号。源表没有渠道/交付方式/承运方，所有凭证为空且物流号仅部分存在。将订单留在待付款状态不会扣库存；人为设为已交付会绕开付款/履约校验。当前API没有可无凭证地写入历史销售出库的入口，不能声称完整流水已经入账。
- Customer.owner 为必填外键，且服务端要求有效销售人员；2条客户归属空缺不能猜默认人。创建客户的服务也要求联系人；10条联系人缺失只能在专用导入规则允许时保留空文本，不能放宽正常客户API。另有1个出库往来方不在客户主档；普通订单API不能在不建客户时提交来源名称快照。须决定补资料或采用获批的历史导入表示方式。
- 销售单要求Channel外键且无默认值；源表没有渠道字段。源物流号不足以区分快递与现场，也不能推定承运方。上述信息缺口不是空字段可安全忽略的情形。
- 用户已确认采购及出库运费缺省按0处理，沿用既有规则，不因源表没有运费列而重问；若源行本身有明确运费值才映射该值。期初/入库的预留、囤货、破损数量仍未确认是否存在，需从来源补明或由负责人向用户确认，不能把这部分缺失值自动置0后声称可售量准确。
- 模型将 `created_at`/`confirmed_at` 记为实际服务端写入时间，成本规格明确操作序号决定生效顺序。已明确保留来源业务日期与本次实际导入时间两个事实，不回填覆盖 `created_at`；待调货语义收口后，由负责人按最小数据模型和验收设计实现，不搭通用框架。
- 另有两组产品身份的别名冲突；不自动合并。仓库按用户已核准的隆生广场仓及现有地址复用，不重问仓址。
- 用户已确认期初和至10月19日全部流水完整，单仓为隆生广场；472个无任何期初/流水的SKU可按无持有数量处理，候选中不造库存余额。
- 单据模型的 `actor`/`created_by`/销售人员都是正式用户外键。使用保留的私有批准名单，将来源经手人仅作姓名键与现有用户精确匹配；只有唯一精确匹配才自动映射，歧义或无匹配时报告负责人。经手人不自动等同客户归属销售。

### 建议的候选方案与保护

候选阶段获准后，从当前已验证恢复点克隆独立数据库并保持原库只读；保留原有认证用户、密码哈希、组/权限关联和账号档案，不重建或重置9个正式账号、不复用/激活技术actor。候选中才重置业务表与控制状态，应用仍固定在71cf9f6/0015。所有业务分类、必填映射和成本前提先通过只读预检再导入。

每个来源单据/分组以工作簿摘要、工作表及源单号/行键确定稳定UUID `operation_key`，另存权限受限的私有映射清单（来源行→操作键→业务单据/流水ID）。同一键同一输入重试由 `BusinessOperation.operation_key`/`input_hash` 幂等返回；同键异输入应409失败；不以重新编号或删历史绕过冲突。候选按来源生效顺序写入，利用业务锁/序号与现有移动平均回放。逐项核对源行覆盖、数量与金额精度、单据/流水关联、最终仓-SKU余额及账号/角色指纹；任何差异即丢弃候选并从保留恢复点重建，不回滚或清理原库。正式切换需负责人另排窗口，当前无切换授权。

### 后续由负责人收口

11组配对的业务含义已由用户收口为“外部合作方采购＋普通销售”，不得再按内部调拨或净额处理。其余仍需收口：历史销售无付款凭证时的状态表示和专用导入如何保存缺失的渠道/交付/物流字段，同时保持日常API原校验；缺联系人、缺归属及未建档往来方的客户主档/快照表示；预留、囤货、破损等限制数量是否实际存在；两组产品别名冲突。采购/出库运费、完整期初中无流水SKU按零持有量、单仓身份/仓址及来源日期与导入时间分开保存均已有明确决定，不再重复提问。规则收口前只保留源清单，不把未知项转成正式交易事实。

## WORKFLOW-SIMPLIFY-1019｜后端只读核对

依据：负责人派发的 `WORKFLOW-SIMPLIFY-1019`、候选人工操作清单 `docs/project/WORKFLOW_SIMPLIFICATION_20261019.md`、权限共识及当前应用 `71cf9f6` / schema `0015`。通过代码图核对服务端校验、权限与事务，并对订单详情、采购、预留、库存调整模板作定点复核；仅写本角色WORK。未改应用、数据库或服务，未运行测试。

### 实现入口与实际缺口

- 本轮列出的主线和库存调整都有后端动作及页面：销售下单/编辑/取消、收款登记/核对、快递发货/销售确认收货/现场交付、提成复核均可从订单表单或详情进入；采购入库有预览/确认页；预留创建/释放同页；拆箱、赠送、破损、盘点、成本纠错均有对应API和调整页控件。未发现这些指定动作缺少服务端入口或独立操作页。
- 现有页面把低频库存动作全部铺在 `adjustments.html` 长页中；库存行没有带入仓库/SKU的就近操作入口。这是导航和上下文缺口，不是后端动作缺失。可先按“选择要做的事”收起其他模块，再从库存行跳入并预填当前仓库/SKU；仍保留各动作分开的输入、流水和校验。具体页面改动仍依 UI 原型审批及负责人安排。
- `adjustments.html` 有盘点状态“已拒绝”的显示分支，但当前 `StockCount` 模型只允许 `pending/approved`，数据库约束也只接受这两种状态，且没有拒绝操作/API。此显示分支不可达；当前盘点退回修改应走待审批记录重核，不能在人工操作清单中描述为支持拒绝。
- 订单详情有三种后端会拒绝、但模板仍可能显示的按钮：①有发货权限且订单待发货时，不判断收款凭证是否存在、图片是否齐全、金额版本是否过期或已暂停；②有核对权限且支付未通过时，不先判断账户责任是否属于当前核对人；③订单完成后仅按查看/复核权限显示提成复核，未先判断当前收款核对是否通过。服务端分别在发货、核对及提成复核动作中拒绝不满足项。建议模板用已有订单/收款/账户事实显示具体等待项并隐藏或禁用当前不可办按钮；保留API权限与业务校验，不新增状态或审批层级。
- 核对责任按账户类型执行：对公由财务/有权者核对，私账由老板核对；普通 `verify_payment` 权限不应让错误责任人看到可提交按钮。提成复核仅在订单完成且当前收款已核对通过后有效。

### 需要保留的业务门槛

- **订单与收款**：下单预览后确认，在事务中重新验证可售库存并占货；订单内新建客户可同事务完成，避免先去客户页重复建档。收款登记是“记录实收账户、金额和凭证”，至少一张图片，金额与订单不一致需原因；它不等同于财务核对到账。
- **履约顺序**：快递发货必须有当前金额版本的收款记录及图片，已标记问题/暂停时拒绝；收款仍待核对但未标问题时允许发货，核对并非每单发货的常规前置。整单发货必须且只能覆盖订单已有的全部供货仓，保留承运方、物流号和多仓对应。销售确认实际收货是另一动作，要求本人且订单已发货；现场交付则在实际交货时一次扣库存并完成，不要再另设收货步骤。
- **采购与限制库存**：采购需活动仓库/供应商、非空明细、正数量及采购价；运费默认0，预留/破损只按实际填写且合计不超入库量。预览/确认须保持同一输入令牌及幂等键保护。客户预留/囤货只能从可售中锁定；释放须选具体记录和数量，不超余量，支持部分释放且不自动到期；订单占用由下单产生，不要再手工锁一次。
- **库存例外**：拆箱要有来源/目标仓-SKU、来源/目标数量和原因，目标数量由人员按实际转换录入；赠送需原因与明细并扣成本、不形成销售收入；破损换货确认实物到达，退款须有实际退款额及处理数量，未退运费默认0。盘点以实盘数量和原因提交，零库存盘盈且选人工成本时需单位成本；审批依提交版本及当前库存版本，并防普通自批。采购/期初纠错先对原单预览影响；有影响必须走审批，审批时序号/余额已变化则拒绝，零影响可直接生效。不能改成直接编辑余额或自动替员工判断差异归属。
- **提成**：金额、运费或后续纠错影响当前计算值时须使已有复核失效/待复核；提成复核仅核定系统计算结果，不替代线下资金处理。不同真实事实仍分步确认，不能把“登记收款、核到账、发货、客户收货、提成批准”合并成一个自动完成动作。

### 可合并的界面呈现与重复输入

- 同一订单页可连续引导销售登记凭证，再显示仓管发货/本人收货的下一步；确认回执后刷新摘要并突出下个有权动作。不同角色可以从同一单进入，但谁能执行仍按订单归属、权限、账户责任和状态判定。
- 订单和采购的预览→确认是同一输入的二阶段核对，不是重复录入；可简化摘要与按钮反馈，不能删除二次服务端检查、版本校验或幂等保护。订单内创建客户已经避免了下单前跳客户页的一次重复操作。
- 仓库在订单行已选定，发货时不应让人重新决定供货仓；应据此预填物流仓库关联，但仍由人确认真实多仓、多物流映射。实际承运方和单号仍需人工录入。
- 出库运费目前可在发货、确认收货及发货后的运费调整中处理；QA已确认收货接口会保存运费。本方案保留收货时补录能力，可收进折叠区并展示已填值，避免同一行无变化时显得必须重录；不能把该输入当无效字段或移除晚到运费的处理入口。
- 采购页可将常用仓库/供应商/SKU/数量/采购价置前，把运费、预留、破损、阈值等条件项折叠；输入过的非默认值及确认摘要必须保留。支付核对/提成复核和库存例外适合按角色/动作聚焦，避免长页滚动与不可执行按钮。

### 交付状态

本轮为代码与页面的只读实现核对，无应用改动、测试或视觉截图。未发现指定后端操作缺失；主要落点是订单详情的三类不可执行按钮和库存调整的就近入口/长页密度。下一步由负责人整合各角色证据，UI按候选方案出具体原型并获批后，再安排单一应用写入者；历史数据重建仍因其他语义缺口暂停，本报告不授权导入。

## COST-AT-OUTFLOW-1019｜后端最小技术合同（只读）

依据：负责人任务及已更新的 `docs/project/COST_COMMISSION_REVISION_20261019.md`；目标仍是运行应用 71cf9f6 / schema 0015。本轮只读代码图、服务/API、回放和报表；未改应用、数据库、公共文档或权限，未运行测试。新决定已替代旧成本规则，以下旧实现仅作改动定位。

### 最小涉及面

| 部位 | 当前实现 | 最小收口 |
| --- | --- | --- |
| 模型与迁移 | `SalesOrder.cost_amount/margin_amount/commission_estimate` 和 `SalesOrderLine.unit_cost_snapshot/order_cost_amount` 都表示下单快照；新订单提交时非空。`InventoryMovement.cost_delta` 与履约操作结果已保存实际出库成本。 | 加一个订单成本口径标识（旧快照/实际出库）；旧记录默认旧口径并保留原值，新订单提交时成本/毛利/提成尚未形成，相关金额及行快照可空。出库后复用订单成本/毛利/提成总额字段保存实际结果；SKU/仓库维度成本从该订单的 `order_outbound` 库存流水汇总，不再加一套成本账表。 |
| 下单及接口 | `sales._plan_order` 固定均价、算毛利/提成；`_order_preview_result` 和 `views._sales_order_data` 返回快照及金额；确认时持久化并占货。`operations.inventory_rows.average_cost` 已返回所选仓-SKU的当前三位成本均价。 | 下单仍校验可售并事务占货，但均价只作为可变参考，不固化为销售成本；预览向销售显示当前仓-SKU成本，不返回正式毛利/提成。保持订单、收款、凭证及服务端校验。 |
| 实际履约与运费 | `_fulfill_inventory` 已在锁定余额下精确算实际扣减并返回金额；`_perform_fulfillment` 仅把它写进履约操作结果。`_apply_freight` 仍以订单时快照重算毛利/提成。 | 发货/现场交付事务内将实际扣减总额写入订单并按“收入－实际扣减成本－运费”算整单毛利，再按整单毛利10%计算提成；金额版本随计算输入变化。展示均价不可乘回替代实际扣减总额，尤其整仓SKU清空时须保留扣尽剩余成本的既有算法。后续运费更正基于已实现成本重算、令旧复核过期；普通运费继续走原重审规则，不生成采购纠错差额。 |
| 纠错与提成 | `CostCorrection.before_after` 已保存影响；`CommissionReview` 保存现核定额及金额版本；`CommissionAdjustment` 保存已确认差额及线下完成状态。当前 `_persist_result` 在纠错审批时会直接更新仍有效的核定额并建差额记录，审批 API 没有独立提成确认动作。 | 纠错审批只做库存/经营成本回放并保持 `CommissionReview.approved_amount`，不等待提成选择，也不创建已确认差额。审批结果可在 `CostCorrection.before_after` 中保存按订单、金额版本绑定的最新提成建议。之后老板通过独立幂等操作选择“调整/保持”；服务端重算差额，不接手填金额。调整才更新核定并创建 `COST_CORRECTION` `CommissionAdjustment`；保持只留决定记录及核定版本，不伪造金额调整。此操作不阻断或撤销已生效的库存纠错。 |
| 回放、报表及导出 | `InventoryReplay._order_input/_finalize_orders` 按订单行下单成本快照重算；`_fulfillment` 已回放实际出库成本，但 `_persist_result` 主要更新行快照及订单快照总额。销售报表 `_sales_data` 按 `line.order_cost_amount` 汇总“下单成本”；提成报表读 `commission_estimate`。 | 回放按订单口径分支：旧记录保留快照语义；新订单事件只占货，实际成本在履约事件从回放余额扣出并生成经营成本/提成。纠错回放更新出库流水、履约结果及受影响订单实际成本。销售报表新口径按订单+仓库+SKU聚合出库流水；未出库新单不报零成本/毛利/提成，已出库使用实际金额。提成报表只对已出库订单显示计算值；保留待审建议、现核定额、已确认差额分列且不重复加总。`build_report` 是页面/导出的共同数据入口，Excel随之校准。 |
| 回归范围 | 现有相关覆盖位于 `crm/test_phase3.py`、`test_phase4.py`、`test_phase5.py`、`test_phase6.py`，覆盖订单快照、实际扣减、运费重审、纠错回放/重复差额及报表。 | 更新这些旧口径断言；新增新单不固化成本、参考价三位显示、现场/快递多仓实际成本、清空尾差、运费按已实现成本重算、纠错审批无须提成选择且不更改核定、独立“调整/保持”确认、当前核定基线、连续纠错增量、重复操作幂等/金额版本过期、旧口径兼容，以及销售/提成报表与Excel不把未出库金额伪作0。只列计划，本轮未测试。 |

### 纠错生效与提成差额确认分为两步

上一版建议把逐单“调整/保持”塞进 `approve_cost_correction`，现按负责人更正撤回：**成本纠错与提成差额确认是两个独立操作。纠错审批不得要求选择，也不能因未确认提成而阻断库存/成本纠错。**

1. **批准成本纠错**：原子应用成本及 `InventoryReplay` 结果；更新系统最新成本/提成计算值及金额版本，保持 `CommissionReview.approved_amount` 原核定额，不写 `CommissionAdjustment`。在现有 `CostCorrection.before_after` 影响快照里，为已核定且受影响订单记录待确认建议：订单ID、现核定额、最新计算额、差额、`order.amount_version`、`approved_amount_version` 和来源纠错ID。未核定订单不生成差额建议，仍等完成/到账核对后按常规审核。
2. **独立确认差额**：新增一个独立提成差额确认 action/API（不扩展成本纠错审批请求）。输入操作键、订单ID、来源纠错ID、期望 `amount_version`、期望 `approved_amount_version` 和 `adjust|keep`；不接收金额。复用现有成本纠错审批责任权限。服务端锁定订单/复核/纠错记录，校验建议仍对应当前应用的纠错及版本，并用最新 `commission_estimate` 与当前 `approved_amount` 重算差额。`adjust` 更新核定额/核定版本，并将非零差额写成关联此独立操作的 `CommissionAdjustment`；`keep` 保留核定金额，把复核版本推进到本次金额版本，并在 `CostCorrection.before_after` 与操作结果记录确认人、决定和版本，不生成虚假的差额行。
3. **过期和幂等**：新采购纠错更新金额版本后，旧建议因版本不符不可操作；新纠错快照基于当时最新计算额和仍有效的当前核定额重新生成。若运费或其他金额输入先变化，旧建议同样过期，但仅进入现有普通 `REREVIEW` 流程，不生成采购纠错差额。确认 action 用独立 `operation_key`/`input_hash`；相同键重放同一结果，改变键重复确认由建议版本/核定版本冲突拒绝。纠错主操作原有幂等不受此独立操作影响；两边任何选择都不表示资金已划转。
4. **展示与授权**：待确认建议只属于纠错影响快照/当前建议，不进入 `CommissionAdjustment` 差额合计；该表仍只记已确认金额变化。建议响应继续经 `visible_cost_correction_data` 按原订单和提成可见范围过滤，不新增角色或扩大提成可见权。后续纠错若再次改变金额，生成新的当前建议，旧建议自然过期，不累加重复扣款。

### 历史与权限边界

- 已验证的当前正式库预检只有期初、没有销售单据，因此本次部署没有历史销售行需要批量改算。迁移默认保留任何既存旧单为“旧快照口径”；新单自发布后才采用实际出库口径。不得全历史清零、覆盖核定金额或假填成本。历史工作簿重建是另一个仍暂停的任务，不由本方案导入或重新解释。
- 若未来某个已有旧单被采购纠错影响，使用正式纠错的事件回放和老板选择处理该影响，不借本次迁移批量重写未受影响旧单。离线历史导入是否采用新口径仍遵循其独立批准与来源语义，不在本任务授权内。
- 不新增补记出货接口，不改销售/仓管自助出货权限；保留收款图片、账户核对、问题暂停、本人确认收货、提成复核资格及库存并发校验。`CommissionAdjustment` 的已确认差额不并入正式核定额总计，不与线下补扣状态混为一项。

实施前交付状态（历史）：当时最小技术面和运行差异已列；计划等待负责人安排实现窗口。本段仅保留实施前背景，当前状态见下方检查点。

### COST-AT-OUTFLOW-1019｜实施检查点（2026-10-19）

- 已按负责人确定的分离流程实现：纠错审批只应用库存/成本回放并保存版本建议；订单提成核定不自动改变。独立 `adjust|keep` action 采用现有老板组/超级用户权限，保留订单可见范围、金额版本和付款核对；重复 `operation_key` 返回同一结果。
- 新接口为 `POST /api/v1/orders/{order_id}/commission-adjustment-confirm`（无尾斜杠；与现有订单动作路由一致）。`offline_transfer_required` 仅在本次 `adjust` 产生非零 `CommissionAdjustment` 时为真，`offline_transfer_performed` 恒为假；本次差额取当前核定额与最新计算额，不累计旧调整。
- 应用改动涉及 `models.py`/迁移、销售预览及下单、履约实际成本分摊、纠错/提成确认、历史回放、报表/API 与 Phase 3–6 回归。新增订单出库前成本、毛利、提成及行成本为 `null`；迁移默认让已有订单继续采用旧快照口径。
- 页面接入待前端单写窗口：`order_form.html` 当前把 `preview.cost_amount` 和 `preview.margin_amount` 直接插入字符串，JSON `null` 会显示成字面量；`commission_estimate` 已用空值占位但需补“出库后核算”的语义。`order_detail.html` 的订单总成本/毛利、提成当前计算金额及行 `unit_cost_snapshot`/`order_cost_amount` 会在出库前为空，需标示未核算。`report.html` 的成本、毛利汇总及对应销售行可能为空，需区分“未出库/未核算”；XLSX 对应金额列应保持空白。`adjustments.html` 也消费纠错影响里的成本/毛利/提成字段，前端需核对空值格式化，不能显示 `null`。
- 已通过 `compileall`、Django `check`、`makemigrations --check --dry-run`、`git diff --check`。在专用合成 PostgreSQL 重跑 `crm.test_phase3` 至 `test_phase6`：118 项中 116 通过、0 errors、2 failures。成本出库、纠错回放/nullable 差异、权限拒绝、确认 API 幂等、旧快照和空值销售 XLSX 等16项定向回归均通过。仅剩两项未改页面的 HTML 断言：`test_home_is_login_only_and_renders_only_currently_authorized_data_blocks` 期望页面包含“今日已交付销售额”；`test_model_status_labels_and_each_status_filter_preserve_order_scope` 对 `<option>` 完整字符串的断言未匹配当前页面。对应模板未修改，需在前端适配/独立 QA 窗口复核，不据此宣称网页验收通过。
- 已按负责人要求结束唯一后端写入窗口的实施阶段；前端待接入字段清单见上。专用 PostgreSQL 测试基库应在交付前清理；不改模板、不部署、不触碰运行副本或业务数据。后续由独立 QA 复核后再由负责人验收。

### COST-AT-OUTFLOW-1019｜连续纠错建议返工（2026-10-19）

- 依据：负责人复核 `1f48aa4` 指出“纠错A未决定、纠错B继续改变金额”时建议断档，并开后端唯一应用写入窗口；前端固定 `f6fba53` 停写。本轮仅改 `corrections.py`、Phase 5 回归及本工作记录。
- `_persist_result` 仅在核定版本仍落后于本次纠错前金额版本时检查上一条已生效建议；只有上一建议的 `expected_amount_version` 精确等于本次纠错前版本、核定版本/金额仍一致、上一目标额仍等于纠错前计算额时，才沿未决建议链生成替代建议。B 仍基于当前有效核定额和本次最新计算额；A 自动因金额版本过期而不可确认。版本不连续或核定基线变化时保持原行为，不继承建议。
- 新增三项回归：A未决定→B替代建议、原核定基线与旧A失效；B的 `adjust` 与 `keep` 都验证同操作键重放幂等；A→运费→B不生成成本纠错差额建议，普通提成 `REREVIEW` 仍可完成。另复跑既有“运费后旧建议失效”与“每次纠错后立即决定”测试。
- 验证：Python 3.12.13；Django check、`compileall`、`git diff --check` 通过。专用合成 PostgreSQL 使用本机 Unix socket `.local/postgres-socket:55439`，基库 `aluola_crm_cost_chain_fix_20261019` 与测试库 `test_aluola_crm_cost_chain_fix_20261019` 均为本轮新建；定向5项全部通过，Django 测试运行器销毁测试库，随后核实无其他连接并删除基库。未使用开发/演示库，未执行全套或浏览器验收。
- 无模型/迁移/API/模板变更，不部署、不触运行副本或业务数据。固定提交后停止应用写入，交 QA 仅对本补丁边界复核并同步负责人；既有两项 HTML 断言仍属于前端/整页验收范围。
- 环境副作用补记（2026-10-19）：初次运行前未记录原 `.venv` 的 Python 版本，因此原解释器版本未知。触发重建的调用为 `uv run --locked --python /Users/chrisz/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 python -c 'import django; print(django.get_version())'`（uv选择Python 3.12.14并重建 `.venv`），随后数据库只读探针调用 `CRM_DB_NAME=postgres CRM_DB_HOST=... CRM_DB_PORT=55439 uv run --locked python -` 又由uv选择默认Python 3.12.13并重建一次。命令记录没有秒级时间；文件系统显示 `.venv` 目录mtime为 2026-10-19 23:27:22 CST，作为最近一次重建时间的近似依据。只读检查时发现PID 72581 使用该 `.venv/bin/python`，cwd 为 `.local/qa_workflow_v2_1019/source`，PPID 97572，启动时间 23:27:53 CST；路径疑似QA副本，但无法确认角色/操作者，也未检查重建当时是否有人使用。未停止该进程，未再同步/重建 `.venv`；已将证据与不确定性告知负责人，由负责人协调受影响的测试/前端环境。

### COST-COMMISSION-BYPASS-1020｜旧提成复核旁路修复（2026-10-20）

- 依据：负责人确认旧 `review_commission` 可把仍有效成本纠错建议直接复核成 `REREVIEW`，绕过老板/管理员独立 `adjust|keep`，并开启后端唯一最小写入窗口。相关后端逻辑在 `368c5cd70e3be59fb3e5532cf52dafd100f1d41b`；本轮工作树基线为 `16a49ed`（含UI页面固定提交 `0ad1b40`），仅改 `crm/fulfillment.py`、`crm/test_phase5.py` 和本 WORK。
- 新增 `_current_cost_correction_commission_suggestion`，复用最新已生效纠错建议 JSON/BusinessOperation 查询；只有建议仍为该订单最新、金额版本与当前订单相同、核定金额/版本与现 review 相同、目标金额与当前计算额相同且无已决定记录时才视为有效。旧 `review_commission` 在幂等重放检查后、写核定前对所有 actor 返回 409 `cost_correction_decision_required`；当前建议过期（如运费推进金额版本）时不拦截，原普通 REREVIEW 保留。新 `adjust|keep` 入口共用有效建议判断，原幂等重放仍先于检查。
- 回归验证：单权限普通用户、双权限普通用户、老板和超管对有效建议调用旧入口均获409且订单/核定/Adjustment/BusinessOperation不变；旧成功操作重放仍200并返回原结果。既有新端点双权限用户403/超管成功及幂等、老板 adjust/keep、连续纠错A/B、运费后普通REREVIEW/旧建议失效均复跑。
- 环境与结果：沿用现存 `.venv/bin/python` 3.12.13直接运行，使用 `PYTHONDONTWRITEBYTECODE=1`/`-B`，未运行uv或修改共享环境。专用合成 PostgreSQL `.local/postgres-socket:55439` 新建基库 `aluola_crm_cost_bypass_20261020`，Django测试库 `test_aluola_crm_cost_bypass_20261020` 已由测试运行器销毁；确认无其他连接后删除基库。相关8项定向测试全部通过，包含Django system check；未跑全套/浏览器，未访问QA19177/运行库/业务库。
- 无迁移、模板、权限组配置、日志、依赖或部署变更。固定提交后停写，交QA仅复核本缺陷边界并同步负责人。

### WECOM-SSO-SPEC-1020｜单企业首期合同候选（只读，2026-10-20）

- 依据：代码只读基线 `be2dfcc`（包含后端修复 `71d23f9`；研究报告记载运行版 `71cf9f6/0015`，不据此指定员工入口）、部署研究报告 `docs/workspaces/deploy/WECOM-SELFBUILT-RESEARCH-1020.md`、负责人整合的待用户确认方案 `docs/specs/WECOM_SSO_PROPOSAL.md`、现有认证/模型代码图及用户经负责人确认的企业微信企业验证、管理员权限、`onenesscyber.tech` ICP备案事实。负责人报告只读 DNS 检查中建议子域 `crm.onenesscyber.tech` 尚无响应；该子域、所有权验证、HTTPS、OAuth可信域名和 CRM 入口均未配置。本轮不查真实库/员工身份，不复制 userid 或 Secret，不联调、不改应用或环境。
- 代码现状：`config/urls.py` 使用 Django `LoginView`/`LogoutView`；`LOGIN_URL` 仍是 `/accounts/login/`。页面由 `login_required` 和现有 `request.user` 权限保护；Session、CSRF、Authentication middleware 已启用。未配置自定义认证后端或 `CACHES`；`AccountProfile` 是 User 一对一，目前仅有 `version`，代码未保证每个 User 都有 Profile；EmployeeAdmin 维护 `is_active` 和角色组。首期沿用这些认证/授权，不把企微应用可见范围当 CRM 白名单。
- 候选路由/API形状（浏览器重定向，不新增 JSON API）：企微 H5 主页配置为 `GET /accounts/wecom/login/?next=/`；服务端生成随机 `state`，连同不超过5分钟的有效期和已净化的站内 `next` 存在当前 Django session，再 302 到官方 `snsapi_base` 授权页。回调为固定 HTTPS 主机上的 `GET /accounts/wecom/callback/?code=…&state=…`。先验证当前 session 中的 state、时限及一次性，再由服务端调用 `gettoken`、缓存 token 并调 `auth/getuserinfo`；仅接受企业成员 `userid`。回调虽为 GET，但会建立认证 session，属于安全敏感写操作。token 经 Django `cache` 按平台 `expires_in` 减安全余量缓存，绝不传前端或写日志；当前默认缓存是每进程 LocMemCache，合成首期按单 worker 验收，实际目标多 worker 时再按真实获取频率评估，不预建共享缓存。平台错误、非成员 `openid/external_userid`、无绑定、重复绑定或停用用户均拒绝且不建会话/改角色；OAuth `code/state` 查询值不得写入业务/代理日志。
- `next` 只允许已验证主机内的安全站内路径，发起时及回调跳转前均校验，缺省 `/`；拒绝跨主机、协议相对和不安全 scheme。回调不标为纯只读，也不加 `csrf_exempt`；session 绑定的一次性 state 防登录 CSRF/重放。成功后对唯一映射且 `is_active=True` 的既有 User 调 Django `login`（显式选用当前默认 `ModelBackend`），让 Django 轮换会话/CSRF令牌，保留既有组/权限，再 302 到净化后的 `next`。若当前 CRM session 是同一 User，则继续；若映射到不同 User，拒绝静默切换并提示用户显式登出后重试。普通密码登录、登出、POST API 的 CSRF/权限流程保持原状；不全局把 `LOGIN_URL` 改成企微登录。
- 最小模型候选：`AccountProfile` 加可空唯一 `wecom_userid` 字符字段（长度按官方 userid 约束在实现前冻结；PostgreSQL唯一约束允许未绑定行保持 `NULL`），仅一项 schema migration，不回填或批量造 Profile。管理员沿现有 EmployeeAdmin 显式绑定获准的既有 User；若其缺 Profile，仅对该 User 按需 `get_or_create`。不增公开自助绑定/创建账号 API，不自动授角；不查真实库确认 profile 覆盖率。
- 首期范围与验收：先在隔离合成环境验证；负责人本人和一名销售是试点建议，使用合成 CRM 账号，不默认正式库、18888 测试服务或真实员工切换。管理员手工设置 `is_active=False`，不做企微通讯录/离职同步；回调拒绝停用用户。当前默认 Django `ModelBackend` 会在后续请求加载 session 用户时检查活动状态，但本轮未实测；QA需用同一浏览器 session 完成登录后停用账号，再访问受保护 HTML/API，确认会话失去访问，并检查角色/CSRF/登出不变。合成验收还覆盖绑定唯一性、缺 Profile 补齐、非成员/未绑定/停用拒绝、state错/过期/重放、code/token失败无会话、外部 `next` 拒绝。
- 真正未决：用户是否接受“工作台 H5 + SSO、保留密码登录”，以及是否接受 `crm.onenesscyber.tech` 为候选并先做上述隔离试点；最终目标 CRM 环境/版本和员工入口（新版业务数据尚未切换）；最终子域名、DNS/所有权验证、HTTPS及企业微信单一回调域名配置；CorpID/AgentID与自建应用配置。管理员按获批范围维护显式绑定/停用。Secret 由管理员在获批实施时安全配置，不索取、不入文档。该记录是供负责人整合的技术候选，不含实施、生产配置或正式入口切换授权。

### WECOM-SSO-IMPLEMENT-1020｜后端固定交付

- 依据：用户批准的首期合同及负责人 `WECOM-SSO-IMPLEMENT-1020` 唯一后端写入安排；工作树基线见本轮固定提交的父提交。只改企微账号绑定/认证、配置、迁移、合成回归及本 WORK。官方成员创建文档确认 `userid` 为 1–64 字节、首位字母或数字、其余仅允许字母/数字/`_-@.`，且唯一性忽略大小写；官方身份接口说明授权 `code` 单次使用并在5分钟过期，成员返回 `userid`，非成员返回 `openid`/`external_userid`。来源：[企业微信创建成员](https://developer.work.weixin.qq.com/document/path/90195)、[获取访问用户身份](https://developer.work.weixin.qq.com/document/path/91023)。
- 实现：`AccountProfile.wecom_userid` 可空，按官方字符集校验，并由 `Lower(wecom_userid)` 条件唯一约束保证大小写不敏感绑定；迁移 `0017_accountprofile_wecom_userid` 接在当前 `0016_cost_at_outflow` 后，无回填。EmployeeAdmin 仅在编辑已有员工时提供绑定字段；未绑定员工不补建 Profile，绑定时按需建行，空值解除绑定。
- `GET /accounts/wecom/login/` 使用 session 保存随机 state、5分钟时限及清理后的站内 `next`，只跳转到配置中的固定 HTTPS 回调；`GET /accounts/wecom/callback/` 校验唯一 state/code、时限和一次性，再用服务端缓存 token 调官方身份接口，仅登录已绑定且启用的既有员工，并保留原 Django 组/权限。当前账号相同可继续，不同则返回带 CSRF 退出表单的409；未绑定、非成员、停用和上游错误均不建立新会话。密码登录与现有 `ModelBackend`/权限流程保留。
- 环境边界：`USE_X_FORWARDED_HOST=False`；`SECURE_PROXY_SSL_HEADER` 仅由显式 `DJANGO_TRUSTED_PROXY_SSL_HEADER=1` 打开，须等反代确实覆盖 `X-Forwarded-Proto` 且 Gunicorn 只监听 loopback 才可设置。本轮未启用全局 Secure Cookie，不改 Nginx、域名、DNS、TLS、企微应用、运行数据库或部署；未使用真实CorpID、AgentID、Secret、成员身份或平台网络。平台调用全部 mock。上线前仍须确认反代访问日志不保存 OAuth 查询参数，并在目标 HTTPS vhost 单独核实 session/CSRF Cookie 的 Secure 属性与原 HTTP 18888 行为；当前 Nginx 访问日志配置未在本任务修改或验证。
- 验证：Django `check` 通过；`makemigrations --check --dry-run` 为无额外模型差异；使用现有 Python 3.12.13、PostgreSQL 专用 socket 和随机合成测试数据库运行企微与密码登录 CSRF/安全 next、角色生效、员工管理权限共18项，全部通过，Django 自动销毁本轮测试库。另确认绑定大小写唯一、多个 `NULL`、state错/过期/重放、伪造转发头不启用、session 撤权、显式退出均覆盖。未跑全量测试、浏览器或真实OAuth。初次尝试名下已有的旧测试库未确认归属，未接受删除提示、未触碰该库；后续回归换用空闲随机库名。
- 固定提交后应用停写，交独立 QA 按该固定版本复核后同步负责人。现实域名/HTTPS/企业微信配置与代理日志要求未满足前，不声称真实企微可用，也不执行运行库迁移或入口切换。

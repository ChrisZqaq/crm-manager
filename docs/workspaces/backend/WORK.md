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

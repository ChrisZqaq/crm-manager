# 测试当前工作

更新：2026-10-08；当前任务QA-UI-INTEGRATION。前端固定提交 `bfb51ece4530ad90e9a73f997769a309946ed5f8` 六页验收曾发现订单筛选初始化缺陷；修复提交 `d85983902d15f676dce68e8d578ff1e00a2497f5` 已完成独立定向复测，9项自动测试与真实浏览器场景通过。此前六页通过证据保留；本轮未重跑无变化的六页。等待负责人审查，不宣称整体验收完成。此前各阶段记录保留为历史。

最近导航b6bc1e8独立浏览器、定向1项和只目录权限四目标403探针通过，负责人已验收发布f477c07；此前库及临时资产清理。

后续测试：收到完整前端固定提交和停止写入确认后核对版本，按已批准范围验证六页真实业务操作、权限/数据范围、桌面与窄屏、关键失败反馈及必要跨模块回归；接口按最新API_CONTRACT。不从候选原型开始测试真实实施，不改应用、不自行派前端编码；结果给负责人，未测项如实报告。等待期间仅维护本WORK，不改应用、不测试漂移版本。

## QA-UI-INTEGRATION｜验收准备

依据：用户批准的 static-v3 六页及唯一审批记录见 [UI WORK](../ui/WORK.md#唯一审批栏)；实施范围和既有业务约束见 [HANDOFF](../ui/static-v3/HANDOFF.md)。HANDOFF 固定文档提交 `8de32876b19d847faa0d837796bebedaf28bfb6f`。不以 v1/v2 或未固定的实现版本作验收基线。

查询子阶段开始前：收到后端固定应用提交、自测证据及隔离环境说明；其起点应为 `4acccd79bd0825afc9f8211c68f15a3bba31184e`。核对实际 HEAD 与交付提交一致、应用写入已停止；漂移则暂停该轮并报告负责人。首页、趋势及库存摘要的响应字段结构须由后端提出、负责人冻结并记录进API_CONTRACT；未冻结的结构不猜测或据此判定。使用本任务新命名的隔离测试库及合成数据，不删除现存 `test_aluola_crm_dev` 或用户演示库，不改应用或仓库测试代码。最终六页集成测试另等前端完整固定交付、后台依赖接入及停止写入。

查询/工作台子阶段（后端UI-QUERY-1固定交测后）：
本子阶段只验证固定后端提交包含的查询及汇总/权限行为；不把尚未重新接入的前端页面或真实视口作为本阶段通过依据，留待最终六页集成。
- 对 `/api/v1/orders` 验证精确 `customer_id`，并同时满足客户可见范围和订单可见范围；同名客户不能串单，也不能因客户过滤扩大订单权限。
- 验证 `warehouse_id` 只匹配活动订单行，跨仓整单只出现一次且不漏单；`salesperson_id` 按订单归属筛选；`q` 搜索既有订单号/ID。
- 验证 `verification_status` 只筛已有收款记录的 `pending/problem/passed`；无收款记录的订单不能被伪归为待核对。组合筛选先作用于授权全集，再计算 `total` 和分页；核对稳定排序与标准分页边界。
- 日期筛选须同时提供 `start_date/end_date`，按 `SalesOrder.created_at` 的北京时间日期且首尾均含；用人工单据日、发货日、更新时间不同的合成记录验证边界与口径，并覆盖成对参数、日期格式和起止顺序校验。
- 工作台对所有已登录角色可进入；无相关权限的角色仍只见无敏感数据的页面壳。逐角色验证各摘要、待办、低库存和业务入口沿用原权限及本人/全部范围，无权数据不请求、不出现在DOM/API中；撤权后的新请求不得继续返回数据，不新增独立首页权限开关。
- 验证四摘要：北京时间当日已交付销售额、待出库订单数、待核对收款数、低库存“仓库×SKU”记录数；最后一项服从全局与SKU提醒开关。7日趋势包含北京时间今天及前6个自然日，逐日金额与同角色范围的已交付销售报表一致，真实无销售日返回零而非假曲线。库存摘要按售卖单位分别呈现在库、可售、订单占用、客户预留、破损，不能跨单位相加。
- 验证销售报表 `series=day` 仅接受 `period=custom` 和成对日期；响应顶层 `series[{date,revenue_amount}]` 升序、金额3位，区间内无销售日补真实零值。用同权限、同已交付范围对照非series报表汇总及逐日结果，并切换分页确认序列/汇总不随当前页变化；366日可用、超366日返回400，非series原行为不变。
- 验证库存报表 `summary.by_unit` 含 `unit/on_hand/available/occupied/reserved/damaged` 整数数量，仅按授权及筛选全集汇总、不受page影响；不同单位不合计。核对 `total` 是仓库×SKU记录数，`low_stock=true` 时为低库存记录数；换页或跨页核验摘要一致。
- 做最小权限、无结果、非法筛选输入及既有订单查询回归；API/首页聚合结构遵循冻结契约，不在前端另算业务指标。

最终六页集成（前端完整固定交付后）：
- 六页（工作台、销售订单、库存查询、商品档案、客户列表/详情、收款核对列表/详情）、共享导航和现有入口；筛选、详情、分页及各页原有业务操作需可实际完成。
- 用现有角色验证数据范围、凭证可见性和核对/编辑操作权限；核对真实数据及单位/金额/状态沿用既有规格，不把图片示例当业务依据。
- 桌面与窄屏布局、列表/详情往返和筛选保留；空结果、无权限、接口/提交失败、加载/成功反馈及重复提交等必要状态。
- 针对改动做最小关联回归，重点守住订单 preview→用户确认 POST、CSRF、`operation_key`/`preview_token` 幂等和服务器校验；不无差别重跑全套。

### UI-LIST-FILTERS｜固定版接口独立核验

契约依据：[API_CONTRACT：UI-LIST-FILTERS接入补充契约](../../specs/API_CONTRACT.md#ui-list-filters接入补充契约2026-10-08待写入交接)。2026-10-08核验固定提交 `a2073d14a0fdd6026461fb15a28358dfbdf4b2de`，父提交为 `17870126ab7ff0aec079cf800fbc264626f481d8`；应用写入已停止。使用独立 QA 数据库 `aluola_crm_qaui_filters_a2073d14_20261008`；未使用、改动或删除 `test_aluola_crm_dev`、演示库及用户模板。此轮只核验集中接口，不作为最终六页集成验收：
- 客户 `owner_id` 精确过滤必须先限于客户授权集，再过滤、计total/分页；非法ID返回既有400，范围外ID只返回空结果。客户 `filter_options.salespeople` 使用完整可见客户owner姓名，选项不受搜索/owner/生日筛选或分页截断。
- 订单 `filter_options.salespeople` 使用完整可见订单salesperson；`warehouses` 只从同一授权订单集的活动订单行供货仓生成。以 `page_size=1` 和筛选组合确认选项仍完整、销售/仓库无越权，多仓订单匹配后整单只出现一次。
- 库存接口暴露既有 `warehouses/skus/brands` 选项及 `{id,label}` 形状；确认选项与完整授权余额查询集一致，不依赖当前页、不扩大独立仓库权限，现有筛选、低库存与单位摘要不回归。
- 商品目录分别核验缺省、`sku_status=active/inactive/all`、非法值：缺省仍仅启用SKU；显式筛选时父商品只在有匹配SKU时保留，嵌套SKU同步过滤，全部模式两类均显示并保留`is_active`。验证mixed SKU、无匹配商品、Product/IP既有范围；共享 `/api/v1/skus` 仍只供启用SKU，停用SKU新增/增量下单拒绝回归。
- 收款API、服务端页面和XLSX核验同一 `q` 筛选集，只匹配既有订单标识/客户名称快照；与账户、首次成功凭证登记日期、核对状态组合时，先在既有授权订单集过滤，再计算total/分页/导出。验证跨页及导出一致、无范围外订单泄漏；`order_status/order_status_label` 与收款`status/status_label` 分列，现有账户选择器沿用。
- `q` 不搜联系方式、SKU或全局客户/联系人；不把无数据结果、只有HTTP 200或当前页客户端过滤判为通过。最终六页还需等前端接入搜索/选项/履约列并修复已登记的收款页溢出后单独交测。
- `UI-PAYMENTS-OVERFLOW` 最终复测：在1280和390视口分别确认根文档 `scrollWidth <= viewport width`；付款表格自己的滚动容器满足 `scrollWidth > clientWidth` 且可水平滚动到末列，所有既有字段完整。不能只凭订单表布局判定通过。

结果：指定6项定向测试全部通过，Django system check无问题。已另跑负责人指定的43项较宽回归，40项通过、3项失败：
- `CatalogTestCase.test_catalog_exposes_reachable_business_links_by_permission`：原始 `/catalog/` HTML不含“新建订单”直达链接，断言失败于 `crm/tests.py:118`。在隔离库中，具 `add_salesorder` 的销售从工作台和订单页都可见新建入口，`GET /orders/new/` 为200；只有目录权限的用户能访问目录，但新建订单为403。目录页未放重复按钮不构成下单功能丢失；原断言应覆盖获批入口及权限，不应要求目录页重复CTA。
- `CatalogTestCase.test_product_api_and_catalog_use_current_display_labels`：首屏HTML不含“瘦包”，失败于 `crm/tests.py:313`。API的 `sku_data` 保留 `pack_type`/`pack_type_label`，但目录详情 JS 没有读取或渲染 `sku.pack_type_label`，因此除断言采用了旧静态HTML口径外，也确有SKU包类型标签未展示的前端缺口。按负责人安排保留原意，待前端接入并以实际DOM验证。
- `CatalogTestCase.test_sku_inner_box_spec_stays_separate_in_search_catalog_and_api`：首屏HTML不含“箱：20中盒/箱”，失败于 `crm/tests.py:406`。API仍返回箱/盒/包规格，目录详情JS选中商品后异步显示这些字段（当前文本格式为“箱 …；盒 …；包 …”）；失败是对初始HTML及旧标点的断言，不证明规格数据丢失。后续应在真实详情DOM中核字段和分列显示。

为核实失败性质，在临时 detached worktree `17870126ab7ff0aec079cf800fbc264626f481d8` 上复跑上述三个失败方法，三项同样失败于相同断言/行。当前提交相对父提交仅改动获批的7个Python应用/测试文件，没有改模板或静态前端，因此上述前端差异未在本次后端接口提交中修复。没有修改应用或现有失败断言，也未做浏览器端最终验收。

清理：测试运行器已销毁各轮临时测试数据库；本 QA 删除仅为本轮创建的两个隔离基库及父版本临时worktree。复核 `test_aluola_crm_dev` 仍保留；未触碰演示库、用户模板或推送代码。

### 最终六页集成｜等待前端新固定 SHA

前端完成并停止写入、负责人给出固定 SHA 后再测；目前只准备范围，不启动应用测试或浏览器检查。
- 对真实 `/catalog/` 异步加载完成并选中商品后的 DOM 验证：SKU `pack_type_label`（瘦包/肥包/空等）可见；箱规格、盒规格、包规格各自对应正确字段、名称及值，混合状态筛选后的子表只显示匹配 SKU。保留原业务断言意图，不因客户端渲染改动而删除断言；将其绑定到真实渲染结果。
- 验证新建订单的真实入口在工作台和销售订单页可见，`/orders/new/` 对具 `add_salesorder` 权限角色可达、对无权角色拒绝；目录无需重复入口。下单 SKU 选择器排除停用SKU，预览仍拒绝新增/增加停用SKU。
- 工作台、销售订单、库存查询、商品档案、客户、收款核对六页分别在1280×720及390×844检查：根文档 `document.documentElement.scrollWidth <= viewport width`；需要横向滚动的表格由自己的容器滚动，且能滚到末列，关键字段完整。记录每页根宽、适用表格容器的 `scrollWidth/clientWidth` 与实际滚动结果。
- 收款核对页单独核实20列表格在付款表格容器内可横向滚到末列，根文档不溢出；不以订单页或其他表格容器的滚动结果替代。另验证付款搜索、履约/核对状态分列及账户/日期筛选保留。
- 按批准范围核对六页真实查询和详情往返、权限/数据范围、空结果、加载/失败提示；只跑新改动所需的回归，不把后端已验收的接口测试无差别重跑。

阻断与升级：UI-QUERY-1及UI-LIST-FILTERS后端接口已按固定版本核验；最终六页仍须确认前端接入授权查询/筛选、展示SKU包类型并呈现真实操作结果，且修复收款页溢出。假数、零值占位、缺少真实查询链路或仅HTTP 200均不通过，工作台及其他页面真实数据/操作未完成前不判六页整体通过。接口事实向后端/前端核实，设计细节向UI核实；业务口径、权限或范围未决时报负责人，暂停受影响项，不自行补规则。

验收边界：UI-QUERY-1与UI-LIST-FILTERS（订单/客户授权选项、库存选项、商品SKU状态、付款q及履约状态分列）后端均按固定版本完成独立核验，语义见 [API 契约](../../specs/API_CONTRACT.md#ui依赖确认补充)。前端完整接入和真实链路/视口验收仍是六页整体通过的必要条件。

## UI-QUERY-1｜后端查询子阶段结果

2026-10-08独立核验固定应用提交 `2d60ca392787f484d784d1daaf71b74939e168aa`；测试期间未改应用。使用独立命名的 QA 数据库及合成数据，Django 测试共25项、24项通过、1项失败：定向订单/工作台与报表用例7项通过，订单状态/日期及报表用例12项通过，独立 API 探针3项通过/1项失败，撤权探针1项通过，低库存 API 探针1项通过。覆盖客户和订单双重可见范围、活动订单行仓库筛选、销售员/状态/q/日期筛选、日销售序列及权限/补零/分页独立、库存 `by_unit` 汇总及低库存开关、首页登录访问与撤权后只读 API 拒绝。Django system check 通过。

唯一失败：`/api/v1/orders?start_date=9999-12-31&end_date=9999-12-31` 返回500而非400，原因是结束日期加一天时溢出。已报负责人登记为 `UI-DATE-OVERFLOW`；该缺陷使本查询子阶段不通过。按负责人安排，已停止继续测试，等待负责人修复并交付新的固定版本后再开始下一轮；不复测已通过项。

负责人已派发 `UI-DATE-OVERFLOW` 共享 `_date_criteria` 最小修复，等待新的固定提交；修复提交到达前不测试工作树漂移。下一轮仅在新固定版本与独立隔离库上验证：订单最大日期不再500且返回受控400/字段错误；共享报表日期路径同样返回受控400/字段错误；正常366日范围仍可用。不得重跑本轮已通过的24项，不触碰旧测试库。

### UI-DATE-OVERFLOW｜修复后定向复测

2026-10-08核验提交 `223fbbcbe1ae12c884a5124f661bdf094bbcff7e`，与负责人固定交付一致；应用代码工作树无漂移。新隔离数据库 `aluola_crm_qaui_date_overflow_223fbbc`，只跑以下定向测试（4项，全部通过）：

```sh
UV_PROJECT_ENVIRONMENT=/Users/chrisz/.cache/aluola-crm-uiquery1-venv CRM_DB_NAME=aluola_crm_qaui_date_overflow_223fbbc CRM_DB_HOST=/Users/chrisz/Documents/projects/aluolaCRM/.local/postgres-socket CRM_DB_PORT=55439 /Users/chrisz/.local/bin/uv run --locked --python /Users/chrisz/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 manage.py test crm.test_phase4.OrderListFilterTests.test_order_date_filter_uses_inclusive_beijing_created_at_bounds crm.test_phase6.ReportDateCriteriaTests crm.test_phase6.ReportPhase6Tests.test_sales_groups_order_lines_per_unit_warehouse_and_snapshot_region
```

验证结果：订单 API 与销售报表 API 对 `9999-12-31` 均返回400且错误字段含 `end_date`；共享 helper 将次日溢出转为 `OperationError` 并带 `end_date` 字段；销售报表 `2024-01-01` 至 `2024-12-31` daily series 返回200、366点。Django system check 通过。测试结束后核对本轮 `test_aluola_crm_qaui_*` 数据库已清理，原有 `test_aluola_crm_dev` 保留；未改应用、未触碰演示库。未重跑先前24项，不包含最终六页/真实视口集成验收。

最终六页、真实视口和前后端集成尚未验收：本轮仅是后端子阶段，前端完整固定交付/依赖接入未完成。探针脚本及缓存已删除；数据库复核确认本轮 `test_aluola_crm_qaui_*` 测试库均已清理，原有 `test_aluola_crm_dev` 仍在。未启动或停止共享服务。

## UI-IMPLEMENT-6｜前端中间固定版独立检查（停止）

2026-10-08核验固定应用提交 `17870126ab7ff0aec079cf800fbc264626f481d8`，与交接版本一致，检查期间应用代码无漂移、无修改。该版本仅作中间交测，不作为六页最终验收通过。用独立合成数据和隔离数据库 `aluola_crm_qaui_impl6_browser_20261008`；Django `crm.test_ui` 3项全部通过，system check通过；`node --check crm/static/crm/app.js` 与提交差异 `git diff --check` 通过。

本地浏览器登录后在 1280×720 与 390×844 检查首页、订单、库存、商品目录、客户、收款核对六页：六个路由均渲染预期主标题，API请求返回成功；窄屏导航可展开。首页合成摘要/待办/趋势有真实响应；订单查询、客户详情与最近订单往返、库存仓库ID筛选、目录详情、收款详情均完成只读交互检查。没有提交业务写操作；控制台无JS错误。

发现并报告负责人：
- 收款核对页在桌面视口的 `document.scrollWidth=2301`（视口1280），窄屏为2107（视口390）；20列列表把整页撑宽，横向滚动落在文档而非列表容器，属于阻断窄屏使用的布局问题。
- 集中待实施的 `UI-LIST-FILTERS` 依赖在该版本未接入：客户 `owner_id=3` 查询仍返回total=2且包含owner=4的数据；客户、订单、库存接口均没有契约所需的销售员/筛选选项；付款接口忽略不匹配的 `q`，仍返回1条；收款页没有搜索控件；库存页面仍要求输入仓库/SKU/IP数字ID。上述为已知依赖缺口，不能据此通过最终验收。
- 用户已确认目录按SKU状态筛选全部/启用/停用且可查看停用SKU，但此版本仍提示目录接口只返回启用商品及启用SKU；合成停用SKU `TST-0002` 不可见。订单新建选品仅显示启用 `TST-0001`、排除停用 `TST-0002`，符合既有下单限制。
- 合成凭证图片在授权测试中，所属销售可读（200 image/png），无权销售得到404。第一次浏览器载入因夹具缺少实际图片文件出现500；补入本轮唯一合成PNG后已验证通过，属测试夹具问题，不作为产品缺陷。

收尾：停止本地8947测试服务。删除本轮专用隔离库和5个探针/夹具脚本及唯一合成PNG；数据库复核只剩原有 `test_aluola_crm_dev`，未触碰演示库。应用代码无修改。最终六页验收仍待前端接入已验收后端、补齐包类型标签、修复收款页溢出并提供新固定交测；本轮测试停止。

### 最终六页集成验收｜固定版 bfb51ece

2026-10-08核验 `HEAD=bfb51ece4530ad90e9a73f997769a309946ed5f8`，与负责人指定版本一致；本轮检查期间无应用代码改动。固定提交相对父提交仅含5个模板与 `crm/tests.py`。使用新隔离库 `aluola_crm_qaui_bfb51ece_20261008` 和命名为 QA 的合成用户、客户、商品、SKU、仓库、订单及凭证图片；未使用、修改或删除 `test_aluola_crm_dev`、演示库及用户模板。

回归验证：`CatalogTestCase`、客户/订单相关测试、订单停用SKU搜索与预览限制、订单筛选及报表测试共43项全部通过；`crm.test_ui` 3项全部通过。Django system check、`node --check crm/static/crm/app.js`、`git diff --check bfb51ece^..bfb51ece` 均通过。原三项目录静态HTML断言已保留并改为核对实际业务意图；目录测试随43项回归通过。

浏览器验证：负责人和销售账号均登录真实页面，在1280×720和390×844逐页检查首页、订单、库存、目录、客户及收款核对。下表为各页根文档 `scrollWidth / 视口宽度`；均未发生页面级横向溢出。表格列出宽表内容宽/滚动容器宽（桌面/手机），其他未溢出表格省略：

| 页面 | 根宽 1280 视口 | 根宽 390 视口 | 宽表内容/容器宽（桌面 / 手机） |
| --- | ---: | ---: | --- |
| 工作台 | 1265/1280 | 375/390 | 720/669；660/349 |
| 销售订单 | 1280/1280 | 390/390 | 1046/1046；660/364 |
| 库存 | 1265/1280 | 375/390 | 1031/1031；989/349（单位汇总表：720/669；660/323） |
| 商品目录 | 1280/1280 | 390/390 | 720/651；660/364 |
| 客户 | 1280/1280 | 390/390 | 720/651；660/364 |
| 收款核对 | 1265/1280 | 375/390 | 2163/641；2163/349，手机滚动 `scrollLeft=1814/1814` 到达末列 |

其余真实页面核验：
- 目录启用/停用/全部筛选分别显示匹配子SKU；混合父商品两种状态和停用SKU独占父商品均正确显示。实际详情DOM显示“瘦包/肥包”标签，箱、盒、包规格分别有名称和值；切换为不含所选商品的筛选后详情关闭。订单选品控件只列启用 `QAT-0001`，不列停用 `QAT-0002/QAT-0003`；43项回归中的服务端预览测试仍拒绝停用SKU。
- 负责人可见两位客户及 `sales-a/sales-b` 负责人筛选项；`sales-a` 只能见其本人 Alpha 客户，没有负责人范围选择器。客户详情负责人为 `sales-a`，最近订单链接到 Alpha 对应订单。负责人和销售账号都能从订单页进入 `/orders/new/`；销售订单页显示履约“待出库”和收款核对“待核对”两列。新建订单未提交。
- 库存筛选项显示具名仓库、SKU、IP；选 West Warehouse + QAT-0001 后概览和列表均为1条，明细一致。收款查询组合日期 `2026-10-08`、客户 `QA Synthetic Customer Alpha`、账户 `QA Public Bank`、待核对，页面显示1行。Django Client 对同参数API响应 `total=1`、`order_id=1`、Alpha；下载并读取的XLSX同样只有该Alpha记录。页面表头完整21列，状态字段在API/页面中分别为履约和核对状态。
- 浏览器控制台无 error/warning。未提交新订单、收款登记、核对或库存设置等写操作。

发现并上报负责人（本轮阻断）：直接打开 `/orders/?salesperson_id=3&warehouse_id=2` 时销售/仓库控件最终显示 `sales-b` / `QA West Warehouse`，但服务日志中的首个列表请求为 `/api/v1/orders?page=1&page_size=50`，漏掉两个筛选参数；页面因而显示全部2单，且URL被改成仅含分页参数。点击“筛选”后请求包含两个参数，结果正确为1单。表现与模板在选项加载前读取控件值、requestedValue应用过晚相符；未修改应用。固定版整体判定不通过，需修复后重新交测。

收尾：已停止本地8951 QA服务；删除本轮隔离库、两张唯一合成PNG和下载的合成XLSX。复核 `test_aluola_crm_dev` 未触碰；应用代码无改动。未测试订单/付款实际写入流程、真实业务数据或支付提交。

### UI-ORDER-INITIAL-FILTER｜修复后定向复测

2026-10-08核验固定提交 `d85983902d15f676dce68e8d578ff1e00a2497f5`，与负责人交付SHA一致；提交仅改 `crm/templates/crm/orders.html` 一行，工作树应用文件无漂移。使用新隔离库 `aluola_crm_qaui_orderinit_d859839_20261008`，创建 qa-owner、sales-a、sales-b、East/West Warehouse 及53笔合成订单：sales-b在West 51笔、在East 1笔，sales-a在West 1笔。回归只跑 `crm.test_phase4.OrderListFilterTests` 与 `crm.test_ui`，共9项全部通过，Django system check无问题。

真实浏览器桌面定向结果（负责人视角，订单列表每页50行）：
- 直达仅 `salesperson_id=3`：首个API请求包含该参数；控件为 sales-b、仓库为全部；总数52；地址栏保留筛选。
- 直达仅 `warehouse_id=2`：首个API请求包含该参数；控件为 QA West Warehouse、销售为全部；总数52；地址栏保留筛选。
- 直达双条件 `salesperson_id=3&warehouse_id=2`：首个API请求同时包含两项；控件分别为 sales-b / QA West Warehouse；总数51，第1页50行。
- 点击第2页后显示1行、总数51，地址栏保留双条件和 `page=2`；刷新仍为第2页、1行，控件与总数保持一致；服务日志的刷新首请求也带双条件和 `page=2`。
- 将销售员改为 `sales-a` 并筛选后，仓库仍为West、结果为1单，URL变为 `salesperson_id=2&warehouse_id=2&page=1&page_size=50`。清空销售员和仓库再筛选后，两控件均为“全部”、总数53，URL只剩分页参数，未残留暂存的requestedValue。

手机视口390×844双条件直达仍为51单；根文档 `scrollWidth=375`（不超过390），订单表容器 `clientWidth=349 / scrollWidth=660`，实际横滚 `scrollLeft=311` 达到末端；桌面1280×720根文档 `scrollWidth=1265`，订单表容器1031/1031。浏览器控制台无 error/warning。固定版整体没有重跑；此前六页通过证据保留，完整整体验收状态交负责人审查。

收尾：停止本地8951 QA服务、恢复浏览器默认视口并关闭临时标签；删除本轮隔离库。测试运行器已删除自己的测试库；复核原有 `test_aluola_crm_dev` 保留。未修改应用、未推送，未触碰演示库或用户模板。未复测真实业务数据、订单写入流程或其他六页。

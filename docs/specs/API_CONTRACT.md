# 第一版网页与API合同

> 2026-10-09增量开发合同已收口，见本文新增章节及[本轮需求](../project/USER_REVISION_20261009.md)。获批UI见revision-v1/HANDOFF；下列增量是开发目标，尚未实现或发布。既有金额、库存及权限规则继续有效，旧阶段验收状态以开发计划为准。


状态：2026-10-05阶段1—6已通过负责人验收，固定应用fd3c991；四报表、Excel导出及Q07收款日期筛选已实现并独立验证。本地验收不代表上线。

## 1. 公共格式和权限

基础路径`/api/v1/`，JSON使用UTF-8。ID及金额用字符串，数量用JSON整数；金额响应固定3位，日期`YYYY-MM-DD`，时间带时区。拒绝空白必填、非法整数、非有限金额及容量溢出。服务端统一时区Asia/Shanghai。

复用Django会话登录：网页及同源API遵守CSRF，正式远程访问HTTPS。第一版不另建Token／OAuth平台；未接入小程序和Agent的外部登录方式为后续接入事项，不能将API匿名开放。已授权Agent查询可复用本版查询合同，但自然语言写操作不提供。

列表默认`page=1&page_size=50`，每页上限200，按稳定ID排序；响应`{items,total,page,page_size,queried_at}`。导出不局限当前页，导出全部当前筛选结果及权限范围。技术分页上限不限制业务总数据量。

写操作提交`operation_key`（UUID）及已有记录的`expected_version`。新增创建也必须防重复；同标识不同输入返回409。修改冲突不覆盖别人的数据。只读GET无副作用。

错误格式`{error:{code,message,fields,details},request_id}`；details中的单据ID须已授权。400输入错误；401未登录；403无动作权限；404不存在或不在可见范围；409库存不足／版本改变／状态冲突／重复标识内容不同；500服务器异常仅返回中文提示和请求ID，不暴露堆栈。字段路径如`lines[1].quantity`。

## 2. 查询与基础资料

| 路径／方法 | 输入和输出 | 权限 |
| --- | --- | --- |
| GET `products`、`skus` | q、brand_id、series、type、language；返回产品身份、别名、SKU编号、单位和规格，不自动选择歧义结果 | 所有登录人员 |
| GET `inventory` | warehouse_id、sku_id、brand_id、low_stock；返回在库、占用、预留、囤货、破损、可售、成本金额及3位平均成本展示值、单位和查询时间；计算仍保留高精度 | 所有登录人员，成本不隐藏 |
| GET `holds`、`damaged-records` | 按warehouse_id、sku_id筛选；返回记录ID、版本、类型／原因、原始及剩余数量，供指定记录释放或破损处理 | 库存查询可查看；实际释放／处理另查动作权限 |
| GET `stock-counts`、`cost-corrections`及`{id}` | 状态筛选，详情含提交版本、待确认影响及允许动作；纠错订单影响只展示授权范围 | 相应登记／提交／审批权限 |
| GET `warehouses`、`brands`、`suppliers`、`channels`、`payment-accounts` | 返回启用选项及必要显示字段，无账户登录秘密 | 有相应业务用途的登录人员 |
| GET `customers`、`customers/{id}` | q包含匹配姓名／别名／公司／联系方式；返回当前授权档案，不跨销售合并 | 本人／查看全部客户 |
| GET `orders`、`orders/{id}` | salesperson_id、customer_id、status、channel_id、日期；详情含行、凭证授权引用、核对及提成状态 | 本人／查看全部订单 |
| GET `regions` | 固定初始化省市列表，城市从属于省份 | 登录人员 |
| POST／PATCH `customers`及`customers/{id}` | name、contact、company、aliases、note1、note2、invoice_title、invoice_credit_code、invoice_email、addresses；地址含recipient、phone、province、city、detail；代建指定owner_id | 本人维护／授权代建 |
| POST `customers/{id}/archive`、`transfer` | archive操作；transfer传owner_id，不改历史订单销售 | 归档按维护权限；转交仅老板／管理员 |
| PATCH `settings/low-stock`、`skus/{id}/low-stock` | 全局enabled、threshold；SKU enabled、nullable threshold，不提供三种模式 | 已确认设置权限 |

除本次商品创建接口外，其他基础资料普通增改停用使用框架后台，API不必重复全部CRUD；禁止后台直接改有效库存、成交成本和已确认业务单据。商品`type`为booster、merchandise、gift_set三个固定值，页面对应补充包、周边、礼盒·预组；不提供`contains_cards`字段或选择项；booster及gift_set语言必填，merchandise可空，由人工选择正确类型。

库存无该仓库SKU余额时查询可返回数量0、成本0、均价空值；不可因此允许无货下单。低库存仅对已有库存管理记录判断，不创建所有SKU×仓库笛卡尔积。

## 3. 业务操作

| 路径／POST动作 | 最小输入及生效条件 |
| --- | --- |
| `opening/preview`、`opening/confirm` | 上传模板、确认产品映射及商品类型；preview返回错误定位和数据摘要；confirm传校验预览标识、操作标识，整批一次入账，成功关闭入口 |
| `receipts/preview`、`receipts/confirm` | warehouse_id、supplier_id、可空图片引用、lines[{sku_id,quantity,purchase_price,inbound_freight,reserved_quantity,damaged_quantity,low_stock_threshold}]；摘要零价提示、预留破损校验后一次入库 |
| `holds`、`holds/{id}/release` | 创建传warehouse_id、sku_id、type=customer_reservation/hoard、quantity、reason；release传quantity、expected_version、operation_key，释放量不超该记录剩余量，不保护指定销售 |
| `orders` | customer_id可空、channel_id、delivery_mode、address_snapshot、lines[{sku_id,warehouse_id,quantity,sale_price,outbound_freight}]；销售从登录身份取得，同仓SKU先合计检查并占货 |
| `orders/{id}/amend`、`cancel` | amend传完整新明细及expected_version，减量／增量规则见成本规格；cancel仅未交付，保留收款资料释放占货 |
| `orders/{id}/payment` | account_id、received_amount、image_ids非空、可空received_at、difference_reason；差额原因填写后才可进入待出库 |
| `orders/{id}/verify-payment` | result=passed/problem、problem_reason；对公财务、私账老板；problem使未交付暂停，已交付不退状态 |
| `orders/{id}/ship` | shipments[{carrier,tracking_number,warehouse_ids}]、outbound_freights[{line_id,amount}]；运费保存、amount_version更新、交付及扣库存同一事务；整单交付，仓库全覆盖，暂停拒绝 |
| `orders/{id}/deliver-on-site`、`receive` | 现场交付接受outbound_freights[{line_id,amount}]并同事务更新金额、扣库存及完成；满足收款及未暂停条件；receive只由销售确认快递收货，不再扣实物 |
| `orders/{id}/freight` | 行ID与分摊金额，缺项不得隐含清零；修改提成计算不动库存成本，旧复核失效 |
| `orders/{id}/review-commission` | expected_amount_version；完成＋核对通过后老板确认；已计入重审生成差额并保留原计入日期 |
| `commission-adjustments/{id}/complete` | 所属销售确认线下处理完成；不执行资金操作 |
| `splits` | source_warehouse_id、source_sku_id、source_quantity、target_warehouse_id、target_sku_id、target_quantity；仅可售来源，SKU不同，两端同事务，数量人工填 |
| `gifts` | warehouse_id、reason、lines[{sku_id,quantity}]；扣可售库存和成本，无收入及提成 |
| `damage-resolutions` | damaged_record_id、kind=exchange/refund、quantity；refund填actual_refund、unreturned_freight，exchange在实际替换货到达后确认；数量不超未处理破损 |
| `stock-counts`、`stock-counts/{id}/approve` | 创建实盘及原因、相关版本、必要输入成本；approve刷新影响；普通人员不能审批自己提交的记录，老板例外保留且仍受业务校验；数量版过期需重核，不覆盖余额 |
| `cost-corrections/preview`、`submit`、`{id}/approve`、`{id}/reject` | 原receipt或opening二选一、指定行及修正字段、reason、可选supplemental_count_costs[{count_line_id,unit_cost}]；仓库改动在receipt单头字段，影响全部行；含preview序号及版本；无影响仓管确认，有影响按审批权限（默认老板）确认，失败回滚 |

预览校验标识绑定操作人和输入摘要，不能拿另一份表或修改后的入库数据复用原摘要；第一版不增加采购草稿生命周期。预览结果及图片引用可作为短期提交数据，不能在确认前增加库存。

业务详情响应至少包含记录ID、版本、服务端计算的明细及汇总、可执行动作和不可执行原因；前端动作提示不代替服务端检查。金额、成本、提成、销售身份和库存计算结果不接受客户端覆盖。

### 3.1 阶段3已实现接口

| 路径／方法 | 行为与权限 |
| --- | --- |
| GET `/api/v1/regions` | 返回固定JSON省市层级及行政区划代码；仅登录人员可读。 |
| GET `/api/v1/channels` | 返回启用渠道；需`view_channel`。渠道名称在Django Admin维护。 |
| GET／POST `/api/v1/customers` | 列表支持`q`、`include_archived=true/false`及标准分页；`q`匹配姓名、别名、公司、联系方式。列表默认只含有效客户。POST创建客户，销售归属本人，其他有权人员须提交`owner_id`及完整地址。 |
| GET／PATCH `/api/v1/customers/{id}` | 查看或维护当前授权范围客户；维护传`expected_version`、`operation_key`，地址可多条。 |
| POST `/api/v1/customers/{id}/archive`、`/transfer` | 归档传版本及操作标识；转交仅老板／管理员，另传新`owner_id`，不改订单销售人或历史地址快照。 |
| GET `/api/v1/orders` | 支持`q`、`status`和标准分页，2026-10-08增量增加精确客户/仓库/销售/核对状态及下单日期筛选（完整参数见文末）；销售默认只见本人订单，获`view_all_sales_orders`者可查看全部；仓管默认见全部订单。`status`保留原状态枚举，另返回由模型`get_status_display()`生成的`status_label`；支持全部五种现有状态筛选。 |
| POST `/api/v1/orders/preview`、POST `/api/v1/orders` | 先提交订单输入获取短时签名预览，再以`preview_token`和`operation_key`确认并占货。现场散客可不关联客户或收货地址；快递须有完整收件快照。 |
| GET `/api/v1/orders/{id}` | 返回授权范围内订单、活动明细、成交／订单成本／毛利、付款核对状态及物流供货仓；`status`保留原枚举并返回模型生成的`status_label`。提成预估、正式批准及差额状态仅向本人销售、`view_all_commissions`或管理员提供；不符合条件时从JSON完全省略对应字段。图片仍使用受控image_id读取。 |
| POST `/api/v1/orders/{id}/amend/preview`、POST `/api/v1/orders/{id}/amend` | 预览完整新明细并校验版本；确认使用签名token及操作标识。删行保留为不活动历史行，新行分配新行ID。修改他人订单时，预览的`commission_estimate`仍按提成查看权限控制。 |
| POST `/api/v1/orders/{id}/cancel` | 未交付订单（待付款凭证或待出库）可取消；传`expected_version`及`operation_key`，取消与释放全部占货同事务完成。已登记的付款记录保留。 |

### 3.2 阶段4已实现接口

阶段4写接口统一使用`expected_version`和UUID格式`operation_key`；金额按3位小数，服务端重新校验权限、版本、状态和幂等摘要。收款账户只在Django Admin维护名称、支付方式、核对责任及启停，不存登录凭据。

| 路径／方法 | 输入、行为与权限 |
| --- | --- |
| GET `/api/v1/payment-accounts` | 返回启用账户的ID、名称、方式及核对责任；需`view_paymentaccount`。 |
| POST `/api/v1/orders/{id}/payment` | `account_id`、`received_amount`、`image_ids`（1—20张）、可选`received_at`、`difference_reason`；订单有应收差额时原因必填。需本人订单或`change_all_sales_orders`维护范围，且另有`register_payment`；查看全部订单不授予修改或收款登记。重新登记令旧核对失效，但保留问题暂停标记。 |
| POST `/api/v1/orders/{id}/verify-payment` | `result=passed/problem`；problem须有`problem_reason`。对公账户由仓管／老板核对，老板私账仅老板核对；问题暂停未交付订单，核对通过才解除暂停。需`verify_payment`及账户责任匹配。 |
| POST `/api/v1/orders/{id}/ship` | `shipments[{carrier,tracking_number,warehouse_ids}]`及可选`outbound_freights[{line_id,amount}]`；整单发货，供货仓库须覆盖订单全部已有供货仓，不能另选仓。有当前版本登记凭证（可待核对）且未暂停时允许出库；暂停、旧付款版本或库存不足会拒绝。需`ship_salesorder`。 |
| POST `/api/v1/orders/{id}/deliver-on-site` | 可选`outbound_freights[{line_id,amount}]`；仅现场订单，凭证齐备、未暂停且只交付一次。扣库存、释放占货、运费及完成状态同事务处理。需`deliver_on_site`。 |
| POST `/api/v1/orders/{id}/receive` | 快递订单销售负责人传版本及操作标识确认收货；不再次扣库存。需`confirm_salesorder_receipt`及本人订单身份。 |
| POST `/api/v1/orders/{id}/freight` | `outbound_freights[{line_id,amount}]`；交付后补录／更正。省略行沿用原分摊，显式0清零；仅金额变化时递增`amount_version`并重算毛利、提成，不重算库存成本。需对应发货／现场交付权限。 |
| POST `/api/v1/orders/{id}/review-commission` | 仅传版本及操作标识；订单已完成且当前付款版本核对通过后，老板复核当前提成金额。重审保留首次计入时间，按新旧正式金额记一条差额。需`review_commission`。响应中的`approved_amount`、`first_earned_at`、`adjustment_id`及`delta`仅在当前请求者可查看提成时返回，否则完全省略；幂等重放也按当前权限过滤。 |
| POST `/api/v1/commission-adjustments/{id}/complete` | 所属订单销售负责人传版本及操作标识，登记线下差额处理完成；不执行转账。需`complete_commission_adjustment`及本人身份。 |

订单预览有效期10分钟。预览到提交期间受协调业务序号或相关SKU、仓库、客户地址、渠道、余额及订单版本变化时返回409，旧占货保持不变，须重新预览。订单写操作先锁`BusinessControl`，再按动作锁订单、收款及仓库ID／SKU ID排序的余额；重复仓库SKU先合并数量后校验。实际出库按移动加权平均成本扣减库存并释放占货，与交付、运费和流水写入同一事务；同一请求重放不重复履约。

订单查看和维护是独立范围：列表／详情要求订单查看权限及本人归属或`view_all_sales_orders`；修改、取消及收款登记按本人归属或`change_all_sales_orders`范围，再叠加各自动作权限。提成字段在列表、详情、页面、订单修改预览与复核响应中按当前请求者权限过滤；未授权字段直接省略，不用`null`或零值代替。

交付运费字段省略时沿用已保存分摊（下单初始为0），明确提交的0才清零；已知费用由人员填写，未知仍允许0并后补，不引入运费是否已知的额外状态。交付成功前不能独立确认一个尚未保存运费的新金额版本。

### 3.3 阶段5已实现接口（已验收）

| 路径／方法 | 输入、行为与权限 |
| --- | --- |
| POST `/api/v1/splits` | `source_warehouse_id`、`source_sku_id`、`source_quantity`、`target_warehouse_id`、`target_sku_id`、`target_quantity`、`reason`；SKU必须不同，可同仓或跨仓，来源按可售量校验，扣减成本全部转入目标，单事务且幂等。 |
| POST `/api/v1/gifts` | `reason`、`lines[{warehouse_id,sku_id,quantity}]`；按各行可售量和移动平均成本扣库存，不形成收入或提成；单事务且幂等。 |
| GET `/api/v1/damaged-records`、POST `/api/v1/damaged-records/{id}/resolve` | 查询待处理破损；处理传`kind=exchange/refund`、`quantity`、`expected_version`、`operation_key`。退款另传`actual_refund`和`unreturned_freight`，记录实际退款与库存成本差额，不重复退款。 |
| GET／POST `/api/v1/stock-counts`、PATCH `/api/v1/stock-counts/{id}/update`、POST `/api/v1/stock-counts/{id}/approve` | 创建传`reason`及实盘`lines`，可用移动平均或`manual_unit_cost`；数量变化后仓管用PATCH重核待审批明细，确认当前实物数量并传`expected_version`；审批传`expected_version`及逐行余额版本。实物数量变化使数量锚点过期；普通人员不能审批本人记录，老板例外仍执行库存校验。 |
| GET `/api/v1/cost-corrections`、POST `/api/v1/cost-corrections/preview`、`/submit`、`/{id}/approve`、`/{id}/reject` | 纠错目标为`receipt_id`或`opening_batch_id`之一；采购仓库变更作用于整单，明细使用稳定行号。可传`supplemental_count_costs[{count_line_id,unit_cost}]`补齐需要的历史盘盈成本。预览在REPEATABLE READ快照中生成令牌，并返回按来源、规范化修正事实、原因及补充成本生成的`input_hash`供同内容重试确定操作键；提交仍传本次实际`preview_token`。提交／审批复核版本、业务序号及当前权限，变化后要求重预览。 |

阶段5库存和成本写操作均先锁`BusinessControl`并在PostgreSQL事务内校验；成本纠错按序回放来源及后续业务，当前受限数量必须合法，历史实际库存不得为负。接受与否以独立测试和负责人复核为准。

## 4. 图片与报表

POST `/api/v1/images`支持JPEG／PNG／WebP，每张≤10MB，服务端验证实际格式及解码；返回受控image_id，无公共文件URL。可由入库凭证或订单收款登记授权上传；已关联图片按单据及订单可见范围访问，未关联暂存图片仅上传者可访问。每单最多20张；失败暂存文件按24小时清理，关联旧凭证保留，不因新截图替换或日志轮转删除。

报表页面为`/reports/{inventory|sales|payments|commissions}/`。结构化查询为GET `/api/v1/reports/{name}`，下载为GET `/api/v1/reports/{name}/export`；页面与两个API使用相同的筛选、权限和统计函数。查询API默认每页50行，`page_size`为1—200；`page`从1开始。响应含`items`、`total`、分页字段、`columns`、`summary`、`timezone`、`period_label`、`date_basis`、`start_date`、`end_date`、`date_filter_available`、`queried_at`及`filter_summary`。

销售及提成周期接受`period=1m/3m/6m/12m/all/custom`，或省略period并同时提供`start_date`和`end_date`。滚动周期以北京时间当天为结束日，向前推1、3、6、12个月；目标月不存在对应日时取该月月末。自定义日期首尾均包含，底层查询用开始日00:00包含、结束日次日00:00不包含。日期须为`YYYY-MM-DD`，起始不得晚于结束。`all`不设置日期上下界。

库存报表读取当前实时库存余额、成本、可售量、限制数量与原因，不接受销售日期筛选。筛选为`warehouse_id`、`sku_id`、`brand_id`及`low_stock=true/false`。

销售默认`scope=delivered`，按快递发货时间或现场交付时间筛选，排除已取消订单；`scope=unshipped`显式查看未交付订单，不并入已交付销售，也不适用交付日期周期。筛选可组合使用`sku_id`、`brand_id`、`warehouse_id`、`salesperson_id`、`customer_id`、`channel_id`、`province`和`city`。省市来自订单地址快照。各行按SKU、仓库、销售、客户、渠道、地区及售卖单位分组；不同单位不混加，订单行收入、下单成本和运费仅汇总一次，毛利按收入减下单成本和出库运费计算。

收款核对表按系统记录的首次成功登记收款凭证时间筛选，复用滚动周期及自定义北京时间日期边界；修改截图、金额、账户或重新核对不改变该时间。支持与销售/提成一致的`period/start_date/end_date`，响应提供`first_evidence_registered_at`、明确日期口径、实际起止日期及`date_filter_available=true`。历史记录仅在存在可验证的成功`payment_register`操作记录时回填，否则保留空值，仅在全部周期中显示，不归入日期区间；不使用`received_at`、订单时间或更新时间替代。可按订单维度、核对`status`、`account_id`及`paused=true/false`筛选。金额列区分订单应收、登记收款与差额；`received_at`仍是可空的登记实际收款时间。图片只返回受控编号，内容仍走原单据权限接口。

提成周期对尚未首次计入的订单按提交时间，对已首次计入订单按`first_earned_at`；重审后的当前批准金额仍归原首次计入日期。预估、待复核、已计入、待重审分别标示；待重审同时显示原批准金额和新估算，不能相加。普通重审及采购纠错差额按各自批准时间单独列示，并显示每条差额的线下处理完成状态；差额不重复加到正式提成总额。筛选除订单维度外可用`commission_status=estimate/pending_review/approved/needs_review`。所有提成查询还必须满足订单可见范围与提成专用权限：销售仅见本人；查看全部订单本身不授予查看他人提成的权限，查看全部提成也不扩大订单读取范围。

GET `/reports/{name}/export`及GET `/api/v1/reports/{name}/export`返回.xlsx，忽略当前页并导出全部匹配行；所有过滤与聚合先应用账号权限，不导出凭证图片内容。内部ID、SKU编号、条码及用户文本按文本写入并防止公式执行；金额保留3位并作为数值，包括负数。Excel有效数字超过15位或文本超过单元格上限时明确拒绝导出，不生成被静默舍入或截断的文件；单工作表超过Excel行数上限时要求缩小筛选范围。工作簿含统计口径、实际起止日期（全部周期显示全部日期）和北京时间查询时间。收款核对表补齐后同样展示首次凭证登记的日期口径及实际筛选区间。

2026-10-04 SKU规格补充Q05：可选说明文本按box_spec（箱规格）、inner_box_spec（盒规格，如18包/中盒）、pack_spec（包规格）依次返回；盒规格为空返回空字符串，与已有规格字段一致，不触发数量换算或成本计算。


## UI最小查询依赖增量（2026-10-08，实施中）

static-v3六页已获用户批准；以下是精确查询/获批筛选的接入，不新增业务数据模型。UI-QUERY-1已在223fbbc实现并通过服务端独立验证及负责人审查；前端接入与最终六页集成仍待验证。

- GET `/api/v1/orders` 保留q、status和既有分页，增加 `customer_id`、`warehouse_id`、`salesperson_id`、`verification_status`；各筛选可组合，先应用原订单可见范围和全部筛选，再计total与分页。
- q扩展到现有页面展示的订单编号/ID，不新增订单编号规则；保留原文本搜索字段。
- customer_id精确关联客户FK，不按客户快照名称猜测。使用此参数时同时沿用客户详情可见范围的校验与错误行为，且不能扩大订单可见范围；供客户面板读取最近订单，沿用现有列表排序/分页，不另建最近订单端点。
- warehouse_id按订单当前活动商品行的供货仓匹配，多仓匹配整单只返回一次，不按已作废历史行匹配；salesperson_id沿用订单所属销售字段。
- verification_status只筛已有收款记录的 `pending/problem/passed` 核对状态，不把没有凭证记录的订单伪算待核对，不引入独立付款状态。字段值/ID校验及错误格式遵循现有API约定。
- 2026-10-08用户明确接受：订单日期筛选按下单日期，工作台所有登录人员可进入且各数据块遵循现有权限，不增加工作台权限开关；据此解除日期/首页依赖阻断，销售报表原发货/现场交付口径保持不变。


### UI依赖确认补充

- GET orders的 `start_date/end_date` 使用现有自定义日期规则（同时提供、YYYY-MM-DD、首尾北京时间日期均包含、开始不晚于结束），过滤正式下单时点，不使用人工单据日/发货日/更新时间；API及页面标明下单日期。日期过滤和其他过滤均在分页前。
- `/` 复用现有home渲染独立工作台模板，仅要求登录；无单独工作台权限/模型。按各数据源现有读取权限和本人/全部范围产生数据与入口，无权数据块不渲染，不先读数据再藏DOM；全部无相关业务读取权限的用户仍能看到无敏感数据的页面壳。
- 四摘要复用服务端查询：北京时间当日已交付销售额、待出库订单数、待核对收款数、低库存仓库×SKU记录数；最后一项明确是记录数而非跨单位件数，遵守全局/SKU提醒开关。销售/收款/订单等作用范围保持各原权限。
- 近7天趋势包括北京时间今天及前6个自然日，沿销售报表原已交付金额及同一权限/筛选口径。服务端提供逐日值并有真实零值，不伪造曲线或在浏览器算业务汇总；新增输出不改变原报表默认响应。后端先确定最小可复用输出结构并报告负责人冻结契约，前端按固定结构接入。
- 库存摘要按售卖单位分别统计当前授权筛选全集的在库、可售、订单占用、客户预留及破损；不将箱盒包数量相加，原分页items/total及默认查询保持兼容。输出结构由后端按已有报告summary结构提出并明确后冻结。
- 不新增客户跟进功能或规则；工作台待办仅接入既有订单/核对/提醒。不存在的数据源不是零值，权限不足也不是“待接入”。


### UI-QUERY-1响应结构冻结

- 订单日期明确使用 `SalesOrder.created_at` 这个系统下单时点（不是更新时间），API成对日期参数的北京时间边界沿既有custom日期校验。
- GET销售报表支持可选 `series=day`，必须与custom周期及成对start_date/end_date一起；顶层额外返回 `series: [{date, revenue_amount}]`，金额固定3位，日期按升序且有效零日填0。使用同一未分页、已授权、已交付筛选集，原summary.totals/默认响应不变。日序列最多366个自然日，超限按现有400校验返回；此容量限制不改变非series报表周期。
- GET库存报表的summary补充 `by_unit: [{unit, on_hand, available, occupied, reserved, damaged}]`，数量为整数，来自当前授权/筛选全集，在库与各限制概念沿现有字段。摘要不随page变化，不产生跨售卖单位数量总和。已有total仍是仓库×SKU记录数；当low_stock=true时它是低库存记录数。
- home沿用已有 `/` 路由渲染独立工作台模板，现有数据接口仍执行各原权限；不新建聚合端点或修改数据库模型。


## UI-LIST-FILTERS接入补充契约（2026-10-08，待写入交接）

依据为获批static-v3客户/订单销售选择及收款搜索，非新业务模块；此段是待实施契约，不代表接口已可用。

- GET customers增加 `owner_id` 精确筛选，权限查询集先限定，再按ID过滤，再total/分页。非法ID沿原校验返回400，权限范围外ID只得到空结果，不授予其他客户可见性。
- GET customers/orders响应均补充 `filter_options.salespeople: [{id, label}]`，ID为字符串、label为既有显示姓名。客户从当前可见客户owner集合取得，订单从当前可见订单salesperson集合取得，完整来源不限当前页、不读取全系统用户列表，两种归属不可混用；选项源在各列表的可见范围内、在交互搜索/销售筛选之前生成，避免筛选结果截断选择项。前端显示姓名下拉，内部ID只作为接口值。
- GET payments report及其同源页面/导出支持 `q` 搜索，UI冻结图文案已定义为“搜索订单编号、客户名称”。使用报表当前展示的既有订单编号/ID、订单客户名称快照；沿用现有文本搜索约定，不新增编号规则，不扩到联系方式、SKU或全局客户检索。
- 收款q与已有账户、首次成功凭证登记日期、核对状态等条件组合，在原授权订单范围内过滤后生成完整报表结果、total/分页或导出。日期口径和核对写入权限不变，客户搜索不使用全局客户表扩大结果。
- 不新建通用用户API、权限项、数据库字段或平行报表；后端只在前端固定停写后恢复指定应用写入。


UI-LIST-FILTERS集中核对补充（待实施）：orders.filter_options另含 `warehouses:[{id,label}]`，来自原可见订单当前活动行的供货仓集合，不扩大仓库查看权限、不从当前页推断。GET库存报表直接提供已有内部 `filter_options` 元数据（warehouses/skus/brands的id/label形状沿现有实现），供库存页面按完整授权余额集选择；不新建通用选项端点。用户已确认商品档案按SKU启用/停用筛选并可查看停用SKU，保留既有Product/IP范围和下单选品约束，不新增产品状态规则。目录的显式状态参数及兼容默认值由后端只读核查后冻结；列表/行内/详情使用一致SKU筛选，全部显示两类并标状态。

### UI-LIST-FILTERS最小结构冻结

- GET products增加目录专用 `sku_status=active/inactive/all`；缺省保留原启用SKU行为，非法值按既有目录筛选错误返回400。显式筛选时产品须至少有一个匹配SKU，嵌套SKU同筛选，保留is_active字段；Product/IP原启用范围不变。GET skus及下单启用校验不放宽。
- 收款页面已有服务端渲染的 `filter_options.accounts` 和账户选择器，直接复用，不为本轮新增API账户选项输出。支付行补独立 `order_status/order_status_label` 和“履约状态”列；原status/status_label继续指收款核对状态，不新加履约筛选或改变状态流转。页面/API/导出同源。
- 此冻结仅定义待实施接口；QA结束17870126固定检查前，后端不得开始应用写入。无模型、迁移、新路由或权限项。

实现状态：UI-LIST-FILTERS已固定a2073d14并通过服务端独立定向检查及负责人审查；前端接入和真实六页验收仍待完成。缺省products响应保留旧行为（无匹配启用SKU的产品仍可含空SKU列表）；显式sku_status才要求至少一个匹配SKU，避免改变旧消费者。

## 2026-10-09增量接口（开发目标，尚未发布）

| 接口 | 约定 |
| --- | --- |
| 客户GET/POST/PATCH | note1/note2及invoice_title/invoice_credit_code/invoice_email取代旧生日/兴趣/爱好字段；沿用版本、幂等、归属范围。删除birthdays及me/preferences生日端点、生日筛选和页面动作；不能留可用兼容开关。q补别名。 |
| orders/preview、orders、amend/preview、amend | 输入增requires_invoice及invoice_title/invoice_credit_code/invoice_email；新增订单可用new_customer替代customer_id，两者互斥，均空即散客。new_customer使用客户基本字段（不接addresses，若传入则返回400及new_customer.addresses字段错误，不静默忽略），姓名/contact必填，非销售代建仍明确owner_id。快递地址仅来自order.address_snapshot，原样保存订单快照及新客首条CustomerAddress，客户姓名/contact与收件人/contact分别输入、不推断相同；后续多地址仍用客户API。开票仅抬头必填；散客开票拒绝。新客只在确认事务内创建，预览只读，失败全部回滚，同键重放同客同单。实际改变已有客户开票资料才须change_customer及当前维护范围；选择与客户现有资料相同的值仅存快照，不要求更新权限。requires_invoice=false清本单快照但不清客户资料，不能借订单越权。订单GET返回需求和快照。 |
| GET inventory | 可传order_id辅助编辑（必须有相应订单维护范围）；基础available保持真实未占可售，额外available_for_order=available＋该订单此仓此SKU原活动占货。普通查询不改变口径；预览/确认使用共享相同计算，最终锁内校验。 |
| GET product-languages | 登录商品查看范围，返回id/name选项；普通录商品不支持新增选项。老板/超级管理员在原生管理后台维护。商品读取language仍为名称，筛选language仍接受名称，期初文字精确解析现有选项，未知值报行字段错误。 |
| POST products | 产品共有字段含language_id，skus数组为实际SKU字段；operation_key。同事务创建产品及SKU，至少一个SKU，所有行复用现有格式、语言、单位/条码重复确认和模型校验，要求add_product及add_sku。结果product_id/sku_ids，同键重放不重建。保留输入并报告并发编号冲突。 |
| POST skus | 为已有product_id新增SKU，沿用SKU字段、重复条码明确确认及operation_key，要求add_sku，不修改已有产品身份或编号。 |
| GET skus/number-suggestion | brand_id；仅商品创建权限可查。按既定IP前缀数字后缀找最大号，返回current_max_code/suggested_code（无已有编号时最大为空，建议从0001开始）。只读建议，不预占、不自动分配；保存仍唯一校验。 |
| GET/PATCH opening/control | GET返回enabled/completed/version及可执行动作；PATCH传enabled、expected_version、operation_key，version沿用control.last_sequence。manage_opening权限且老板身份，超级管理员按既有规则；普通获权限者不满足身份仍拒绝。仅未入账可关闭/重开，不要求关入口时空库存；已入账不可重开。确认/开关同锁，关闭后preview/confirm均拒绝，原入账保护保留。 |

新客户建档需add_customer；更新已有客户资料需change_customer及维护范围；订单动作仍需原权限。订单提交输入与开票/新客数据一起绑定预览哈希，禁止确认时替换。新的开票资料没有完整度/税务/开具状态。共用已有错误、权限、事务和日志方案，不新增搜索服务/审计框架。

工作台新布局已批准；客户排行金额、在售组合/新增口径及销售额增速比较周期尚未全部收口，未明确的数据显示—，不得猜值或虚构0。订单卡映射及权限在实施前列明；布局批准不新增状态。

### 增量接入细节（2026-10-09已收口）

- GET products可传include_inventory=true，仍按产品分页，total仍为产品数。请求必须有view_inventorybalance，无权opt-in拒绝；默认响应不变。当前页products→skus[].inventory为[{warehouse_id,warehouse,available,unit}]，只含已有余额、批量查询；无余额数组空，SKU仍显示，有库存查看权限时可售0／仓库—，无权限不可伪装0。不建SKU×仓库记录或拉全库拼接。
- POST products中各skus行及POST skus复用confirm_duplicate_barcode布尔确认。未确认重复条码返回409 duplicate_barcode_confirmation_required，返回已授权候选SKU ID/code/product/language/unit；同次请求内前序尚未保存的SKU候选id=null，按“本次表单前序SKU”及code显示、不生成已有SKU链接。失败不记录成功操作，可沿同operation_key重试并确认相关行。成功后同键不同输入仍409 idempotency_conflict。服务端在事务内重查条码与确认，竞态不能静默绕过提示；详细原子实现及回归由后端固定交付。
- home不新增聚合接口/统计表；有真实已定数据的卡片沿用原接口及权限，排行／商品组合／增速等未定口径显示—。需要后续业务确认的指标单独列明，不阻断已批准页面接入。

客户地址API使用phone字段，订单address_snapshot使用contact字段；前端明确映射，不默认客户联系方式等于收件电话。

订单确认若当次实际更新了已有客户开票资料，同键重放仍须重新检查change_customer及该客户当前维护范围；不能因资料已被首次提交更新为相同值而跳过。未更新客户资料的订单重放不额外要求客户修改权限；新建客户重放仍检查add_customer。沿用已批准的当前权限及撤权重放拒绝规则，历史成功结果不授予新访问权限。

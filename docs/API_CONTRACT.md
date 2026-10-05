# 第一版网页与API合同

状态：2026-10-05阶段1—5已通过负责人验收，固定应用9d52aab；第3.1—3.3节为当前已实现接口，四报表及导出属于阶段6待实现范围。本地验收不代表上线。

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
| POST／PATCH `customers`及`customers/{id}` | name、contact、company、aliases、birthday、interests、hobbies、addresses；地址含recipient、phone、province、city、detail；代建指定owner_id | 本人维护／授权代建 |
| POST `customers/{id}/archive`、`transfer` | archive操作；transfer传owner_id，不改历史订单销售 | 归档按维护权限；转交仅老板／管理员 |
| PATCH `me/preferences` | birthday_reminder_enabled；只影响本人页面 | 本人 |
| PATCH `settings/low-stock`、`skus/{id}/low-stock` | 全局enabled、threshold；SKU enabled、nullable threshold，不提供三种模式 | 已确认设置权限 |

基础资料普通增改停用使用框架后台，API不必重复全部CRUD；禁止后台直接改有效库存、成交成本和已确认业务单据。商品`type`为booster、merchandise、gift_set三个固定值，页面对应补充包、周边、礼盒·预组；不提供`contains_cards`字段或选择项；booster及gift_set语言必填，merchandise可空，由人工选择正确类型。

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
| GET／POST `/api/v1/customers` | 列表支持`q`、`include_archived=true/false`、`birthday_today=true/false`及标准分页；`q`匹配姓名、公司、联系方式。列表默认只含有效客户。生日筛选要求个人提醒开启。POST创建客户，销售归属本人，其他有权人员须提交`owner_id`及完整地址。 |
| GET／PATCH `/api/v1/customers/{id}` | 查看或维护当前授权范围客户；维护传`expected_version`、`operation_key`，地址可多条。 |
| POST `/api/v1/customers/{id}/archive`、`/transfer` | 归档传版本及操作标识；转交仅老板／管理员，另传新`owner_id`，不改订单销售人或历史地址快照。 |
| GET／PATCH `/api/v1/me/preferences` | 读取或修改本人生日提醒开关；PATCH传版本及操作标识。 |
| GET `/api/v1/birthdays` | 返回北京时间当天、当前用户可查看且未归档客户的生日；关闭个人提醒时返回空列表。 |
| GET `/api/v1/orders` | 支持`q`、`status`和标准分页；销售默认只见本人订单，获`view_all_sales_orders`者可查看全部；仓管默认见全部订单。`status`保留原状态枚举，另返回由模型`get_status_display()`生成的`status_label`；支持全部五种现有状态筛选。 |
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

收款核对表的Q07日期归属尚未确认，因此当前显示授权范围内的全部收款记录，不按日期筛选；非`period=all`、`start_date`或`end_date`会返回`date_filter_pending`。响应明确`date_filter_available=false`、`payment_date_basis_pending=true`。可按订单维度、核对`status`、`account_id`及`paused=true/false`筛选。金额列区分订单应收、登记收款与差额；`received_at`是可空的登记实际收款时间，不等同于首次登记凭证时间。图片只返回受控图片编号，图片内容仍走原单据权限接口。

提成周期对尚未首次计入的订单按提交时间，对已首次计入订单按`first_earned_at`；重审后的当前批准金额仍归原首次计入日期。预估、待复核、已计入、待重审分别标示；待重审同时显示原批准金额和新估算，不能相加。普通重审及采购纠错差额按各自批准时间单独列示，并显示每条差额的线下处理完成状态；差额不重复加到正式提成总额。筛选除订单维度外可用`commission_status=estimate/pending_review/approved/needs_review`。所有提成查询还必须满足订单可见范围与提成专用权限：销售仅见本人；查看全部订单本身不授予查看他人提成的权限，查看全部提成也不扩大订单读取范围。

GET `/reports/{name}/export`及GET `/api/v1/reports/{name}/export`返回.xlsx，忽略当前页并导出全部匹配行；所有过滤与聚合先应用账号权限，不导出凭证图片内容。内部ID、SKU编号、条码及用户文本按文本写入并防止公式执行；金额保留3位并作为数值，包括负数。Excel有效数字超过15位或文本超过单元格上限时明确拒绝导出，不生成被静默舍入或截断的文件；单工作表超过Excel行数上限时要求缩小筛选范围。工作簿含统计口径、实际起止日期（全部周期显示全部日期）和北京时间查询时间。收款核对表含Q07待确认说明，不伪造日期区间。

2026-10-04 SKU规格补充Q05：可选说明文本按box_spec（箱规格）、inner_box_spec（盒规格，如18包/中盒）、pack_spec（包规格）依次返回；盒规格为空返回空字符串，与已有规格字段一致，不触发数量换算或成本计算。

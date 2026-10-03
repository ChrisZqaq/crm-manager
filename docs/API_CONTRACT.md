# 第一版网页与API合同

状态：2026-10-03技术总审稿，未实现。网页和API调用同一业务函数；以下路径供开发基线批准，不意味着已上线。

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

交付运费字段省略时沿用已保存分摊（下单初始为0），明确提交的0才清零；已知费用由人员填写，未知仍允许0并后补，不引入运费是否已知的额外状态。交付成功前不能独立确认一个尚未保存运费的新金额版本。

## 4. 图片与报表

POST `images`支持JPEG／PNG／WebP，每张≤10MB，服务端验证实际内容及解码；返回受控image_id，无公共文件URL。GET `images/{id}`必须检查所关联单据权限，未关联暂存图片仅上传者可访问。第一版每单最多20张，用户已确认该技术容量；失败暂存文件按24小时清理，关联旧凭证保留，不因新截图替换或日志轮转删除。

GET `reports/inventory`、`sales`、`payments`、`commissions`，公共日期输入`period=1m/3m/6m/12m/all/custom`或自定义start_date/end_date。月推算遇不存在的日取该月最后一天，查询区间为起始日00:00包含、结束日下一天00:00不包含，统一北京时间。

销售报表默认发货／现场交付口径；未交付仅显式选择unshipped另查，已取消不计有效销售或预估提成。支持SKU、IP、仓库、销售、客户、渠道和收货省市组合；数量按单位分组，毛利按下单成本。客户生日开关不改变访问范围。

提成汇总使用当前正式批准金额及原计入日期；待重审列出旧批准与新计算值，不能两者相加。未首次计入按提交日期；差额清单按批准日期单独展示，无转账日期。采购成本修改可能更新历史销售毛利，但不改变销量、成交收入或发货日期。

GET `reports/{name}/export`返回.xlsx，与页面过滤及权限一致、导出全部匹配行，不含图片包。内部ID、SKU编号和条码为文本，金额3位、单位明确，用户文本不能被识别成公式；响应给出实际起止日期及查询时间。

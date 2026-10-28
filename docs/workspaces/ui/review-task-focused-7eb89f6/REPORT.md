# TASK-FOCUSED-UI-1023：7eb89f6 定向 UI 复验

结论：**原549版本七类返修在本轮指定UI范围通过**。交负责人结合QA剩余操作验收；此结论不代表正式上线或全系统通过。

## 依据与环境

- 获批设计：`../task-focused-v2/`，用户2026-10-23直接批准；唯一审批证据见UI WORK。冻结设计未修改。
- 固定应用：`7eb89f6b98178e582e0c13f98ca715576ba4d233`，实际父 `df16296ea8e0a15d7f9ec90cdfb82a1fc51dc6ac`。
- QA独立合成源码：`.local/QA-TASK-FOCUSED-UI-1023-7EB89F6-1027/source`；只读代理19259，后端19260；数据库由QA管理。仅合成账号，无正式访问。
- 实际CSS指纹由QA固定环境清单核对：`6d1e5aa73be3fe85ca2dd791a38002f09ba68c8f0011421ce7fee0907376c849`。API证据快照为[QA_API_MANIFEST.json](QA_API_MANIFEST.json)，属于QA证据，不冒充UI独立API测试。
- 01—07截图及主体DOM数值实测于10月27日；额度中断后变量丢失，10月28日根据原CUA回执忠实转录为[GEOMETRY.json](GEOMETRY.json)，非源码推算或全矩阵重测。08及其独立字段为10月28日实际补查。

## 七类修复与证据

| 项目 | 实际结果 | 证据 |
| --- | --- | --- |
| 非首项纠错深链 | cost_correction_id=1、order_id=4挂载后聚焦目标ARTICLE；scrollY2304.5，目标top712.27，金色焦点可见；非首条order3 | [01](01-correction-nonfirst-1280.jpg)，deep4 |
| 完整精确数值 | 今日极值¥99999999999999999.999、增速+9999999999999999999800.00%、Top1 ¥99999999999999919.000为实际文本；桌面Home→End位移316.5/436/153.5，手机265/384/127.5；非仅title/aria | [02](02-business-1280.jpg)、[03](03-values-end-1280.jpg)、[04](04-values-end-390.jpg)，keyboard1280/business390 |
| 普通订单编号 | 摘要链接116.75px，订单53/52/1/2编号实际可辨；未沿用旧28—38px结果 | 02，businessDesktop |
| 手机Tab及目标尺寸 | 三Tab同top166.5、约99.67×44，一行完整；核对选择框44，待办链接321×44 | [05](05-tabs-select-green-390.jpg)，verify390/todo390 |
| ship任务聚焦 | /orders/2/?task_action=ship到实际发货表单首个可用SELECT，top400.98、位于#ship-form | [06](06-ship-focus-390.jpg)，ship390 |
| 发货表内部滚动 | 660px表格位于317px内部auto容器；实际键盘到末运费输入使容器left267，输入x147.03..346.03；页面根375/375 | [07](07-ship-table-end-390.jpg)，shipKeyboardScroll390 |
| 八卡、分隔线及根宽 | 桌面/手机八卡均182px、分隔区112px；根1265/1265及375/375，无新增根横溢；米/绿必要相邻样本实看 | 01—07，各geometry字段 |

### 10月28日唯一补查

Top3第2/3金额¥0.999、¥0.001：桌面容器client45、scroll47、tabindex=0，实际Home=0→End=2，两项尾数完整可达。[08](08-small-ranking-end-1280.jpg)及desktopSmallTop3Supplement20261028。此前手机两项client70/scroll70已完整显示，无需额外tabindex。跨日后今日销售额自然变为0、增速-100%，08不替换10月27日极值证据。

## 限制与交接

- 只读观察、键盘和Tab操作，无确认调整、收款、出库、上传或业务POST；未改应用、冻结设计或公共文档，未重跑已通过的后端17项。
- 05内QA合成凭证缩略图未加载；本轮没有验证媒体读取/上传，不据此判定正式应用媒体故障。
- 未测真实设备软键盘、读屏语音、系统减弱动态及完整业务链；低频update_payment/cancel等剩余操作由QA独立收口，本报告不作替代。
- 10月28日初访连接拒绝，经QA恢复服务后成功登录并完成仅剩两项补查；环境故障不记作产品通过或失败。
- 本轮视口已恢复、自建活动测试标签已关闭，浏览器已释放；不停止QA服务，由QA按其剩余验证安排清理。
- 集中报告、geometry和SHA交负责人/QA后，本轮UI证据停止写入；发布由负责人综合验收后安排。

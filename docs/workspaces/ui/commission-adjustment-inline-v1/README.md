# 已生效纠错影响中的提成差额：局部候选 v1

2026-10-20，已获用户明确批准接入；原话、来源与冻结三hash唯一记录在[UI WORK审批栏](../WORK.md#当前提成差额局部接入批准commission-adjustment-inline-v1-1020)。按[最小HANDOFF](HANDOFF.md)交前端，三源冻结；不新增导航、记录页、审批层或金额编辑器。下方描述为获批局部设计及原型证据，不是技术验收。

[打开交互候选](http://127.0.0.1:18774/commission-adjustment-inline-v1/) · [源入口](index.html) · [冻结源指纹](SHA256SUMS) · [证据清单](manifest.json)

## 本次需要确认的呈现

- 放在现有“库存调整与纠错→纠错审批记录→已生效纠错”的影响明细内。外层标题/品牌及一条库存成本变化仅作上下文，实施不重做全页或新增独立详情页。
- 每条受影响订单：编号链接、销售名（仅已有可见数据有时显示）、当前核定→系统新计算、带正负号差额；两个直接按钮“确认调整”“保持原额”。无金额输入，无批量/默认自动决定，无二次审批弹窗。
- 操作提交中该行两个按钮不可重复点击；成功用本次响应替换行内操作为摘要及操作记录标识。调整显示新核定和调整差额/待线下处理；保持显示原核定和“本次不调整金额”，不显示已补扣款。
- 版本过期或条件变化，以短原因及刷新入口替换两个决定按钮；其它独立订单继续可操作。刷新后必须以服务新响应为准，不重新启用过期建议；没有当前建议则显示无待处理项，不能把缺字段/权限不足画成零或待决定。
- 1280横排；390金额组自然换行、两按钮并排、至少44px。沿用米色/靛蓝、现有白底Logo；不扩全站样式。页尾三枚演示按钮/合成标记不进入真实产品。

## 字段与真实接入边界

权威依据：[API_CONTRACT](../../../specs/API_CONTRACT.md)“实际出库成本与独立提成差额”，固定后端`confirm_cost_correction_commission_decision`与`visible_cost_correction_data`；设计不成为另一套业务规则。

| 展示/操作 | 现合同来源 |
| --- | --- |
| 候选行 | `row.before_after.commission_adjustment_suggestions`，只OWNER/超管且原订单可见；已决定/不再匹配版本的建议会被过滤。 |
| 金额 | `approved_amount`、`target_amount`、`delta_amount`，服务三位字符串，保留符号/精度，不客户端重算、不科学记数或截断核心金额。 |
| 订单/销售 | `order_id`链接现有订单详情；候选中的销售名为合成已知展示资料。建议本身无销售名，不批量加载订单补齐，不加接口，缺名即隐藏销售行。原型订单链接只定位本行，不跳真实订单。 |
| 决定 | `POST /api/v1/orders/{order_id}/commission-adjustment-confirm` **无尾斜杠**；原请求`operation_key`、`cost_correction_id`、`expected_amount_version`、`expected_approved_amount_version`、`decision=adjust|keep`，无金额。沿现有幂等/重试，不以按钮禁用代替服务端校验。 |
| 本次结果 | 成功直接返回operation result，无额外envelope；使用`decision`、`approved_amount_before`、`calculated_amount`、`approved_amount`、`delta_amount`及`sequence`/`adjustment_id`。keep的计算差额不是已批准资金调整。 |
| 资金状态 | `offline_transfer_required`仅非零adjust为true，`offline_transfer_performed`恒false。未实际完成不能显示已补扣；沿现有线下处理记录，不新增资金按钮。 |

### 已知实现依赖，不擅补服务

- 当前list不提供完整已决定历史行。候选成功摘要仅表达本次响应的可见反馈；刷新后建议消失不代表记录丢失，原操作/调整记录由服务保留。不新增历史接口/新记录页，不用本地存储伪造历史。演示记录号/老板/刚刚仅合成；真实时间和身份只有既有来源提供时才显示。
- 版本冲突按`stale_suggestion`刷新；条件冲突/付款未核对/撤权按真实中文错误反馈并禁旧按钮。权限继续服务端最终判定，不能仅藏按钮替代授权。
- 页面现有`changes`、纠错原因及审批结果保持原位。没有当前可见建议时不渲染可操作差额组，不让旧review按钮成为adjust/keep旁路。

## 实际观察与限制

本机IAB实际1280×900和390×844，6张截图均保存后查看：[01待处理桌面](screenshots/01-pending-1280.jpg)、[02已处理桌面](screenshots/02-decided-1280.jpg)、[03手机](screenshots/03-pending-390.jpg)、[04过期](screenshots/04-stale-390.jpg)、[05条件变化](screenshots/05-blocked-390.jpg)、[06手机结果](screenshots/06-decided-390.jpg)。手机按钮46px；实际root scrollWidth不超过clientWidth（手机innerWidth390，纵向滚动条使clientWidth375）。版本过期→刷新、分别adjust/keep的合成反馈已实际点击。

所有金额/订单/销售/记录为合成内存状态，无API、认证、真实权限、版本冲突、资金或幂等验收。刷新页面重置；没有测试套件。减弱动态CSS保留但未切系统偏好，真机/读屏/真实极值矩阵未验，不宣称技术通过。

旧18774服务已停止，UI仅重启loopback18774静态预览（根`docs/workspaces/ui`，不暴露仓库根或.local），保留用户看稿。源码指纹固定，现已批准并整理实施交付；真实应用由前端按负责人唯一窗口接入，技术验收及上线另行安排。

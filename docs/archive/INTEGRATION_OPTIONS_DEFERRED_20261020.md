# 企微与移动入口暂缓备忘

记录：2026-10-20。用户要求先专注CRM系统上线，此文仅负责人留存备忘，不列入docs入口、共同规则、角色任务或默认阅读集；其他成员无需读取。未来用户重启该主题时再复核官方规则和平台实际能力。本文件不是开发授权或已确认需求。

## 已完成与实际阻断

- 现有Django系统已有企微H5 OAuth小模块，固定eefc490，隔离mock独立22项通过；不自动创号/授角，显式绑定既有User，原密码登录保留。
- 云测试已部署eefc490/0017，crm.onenesscyber.tech独立证书与HTTPS/密码登录通过，正式业务库未切。个人备案域名被企微可信域名主体校验拒绝；ICP备案存在不等于主体要求满足。企微可见范围已设置、企业/应用标识已收到，具体值与Secret不保存在本文。真实OAuth/成员绑定未联调。
- 账号/业务安全仍用现Django权限、CSRF、库存事务和幂等。保留已部署模块，不为了暂缓而反向删代码、退迁移或停止现HTTPS。

## 候选路线（均暂缓）

1. 同企业或获认可关联主体域名：解决网页可信域名校验后重启H5 SSO。无此域名时先核企业备案与现腾讯云账号资源能否复用，不承诺个人资源可直接为企业备案。
2. 公众号：可以提供H5菜单入口，但H5仍在自身服务器；公众号中转不自动解决企微OAuth主体校验，公众号身份不能直接当员工身份。
3. 企业主体原生小程序：企微关联后可作为表单客户端，后端仍可复用现服务器/API/数据库。使用企微小程序登录验证实际点击者及绑定CRM账号；不同于套H5，不沿用网页OAuth。应先验证小程序后台request域名、企微关联与真实身份、Claw Bot卡片能力，未验证不保证现个人备案域名全部通过。小程序认证/备案/审核与服务器域名配置分别处理。
4. 小程序云托管：官方存在无需配置通讯域名的调用路径，但会增加托管与维护；企微兼容/成本需实际核验，不为解决单一前置问题直接换架构。

## 用户偏好的后续机器人＋表单流程

销售在内部企微群@机器人 → 返回CRM表单链接 → 销售填写并确认 → CRM登记订单占货 → 机器人收到真实结果后回原群报告并提醒下一步。用户当前倾向先普通链接，尚未授权机器人接口或通知开发。

- 先使用已有账号密码/session访问普通HTTPS表单，不将链接中的销售名/ID视为登录授权。群链接可转发；实际点击者必须认证且校验权限，不发可被群内其他人冒用的销售免登链接。
- 机器人若提供可信平台userid/chatid/msgid才可绑定发起人/原群，不能仅凭昵称匹配；官方userid可能为企业范围加密值，需核现Claw Bot透传及转换能力。不联系独立Bot会话或修改其服务。
- 打开链接不创建订单或占货；表单提交成功并事务生效后才回报。通知失败不回滚已成功订单，不因回调重试重复下单/重复通知。订单登记、收款、发货分别执行，不把“已占货”报成“已发货”。群内报告字段范围需再确认，不默认展示成本/提成/客户隐私。
- 官方临时response_url存在次数与有效期限制，不能当永久群发送接口；须核Claw Bot实际回群发送能力。

## 重启时复核的官方资料

- https://open.work.weixin.qq.com/wwopen/common/readDocument/40754 （企微域名主体）
- https://developer.work.weixin.qq.com/document/path/92370 （关联小程序）
- https://developers.weixin.qq.com/miniprogram/dev/framework/ability/network.html （服务器域名与云托管路径）
- https://developers.weixin.qq.com/miniprogram/dev/framework/ability/domain.html （web-view业务域名）
- https://github.com/WecomTeam/MiniProgramSample （企微小程序身份能力）
- https://developer.work.weixin.qq.com/document/path/100719 （机器人消息身份）
- https://developer.work.weixin.qq.com/document/path/101138 （临时回复限制）
- https://developer.work.weixin.qq.com/document/path/101032 （卡片小程序跳转）

只归档讨论与未决边界，不保存人员资料、真实库存或任何凭据。工期仅曾作范围性估算，未形成排期承诺。

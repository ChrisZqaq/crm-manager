# 企业微信 SSO 隔离测试基础设施方案

研究记录始于 2026-10-20。负责人随后转达用户已批准测试环境部署清单。本报告保留只读研究事实，并在“2026-10-20测试部署更新”记录已执行范围。只部署测试应用/测试库；不访问正式业务库、不改业务代码、不输出秘密或读取既有日志内容。

## 只读现状

- **DNS/外网：**当前 `crm.onenesscyber.tech` 有 A 记录指向现有 CRM 主机，无 AAAA。HTTPS 子域请求已实际到达该主机；腾讯云安全组仍未通过 SSH 核验。
- **TLS：**由于共享证书 SAN 不含 `crm.onenesscyber.tech`，已按用户授权使用独立 Certbot `webroot` lineage 签发CRM专用证书。专用证书 SAN/checkhost通过，有效期 **2026-10-20 至 2027-01-18 UTC**；真实域名严格TLS `curl` 返回200且验证结果0。根域与study站点仍使用原共享证书，未更换。
- **Nginx/入口：**Nginx 1.18.0 active，443、80、18888监听；UFW规则未改。独立CRM 80 challenge vhost及443应用vhost均已启用；根域、study、18888配置不变。80 challenge外路径返回404，webroot指向`/var/www/letsencrypt`。CRM HTTPS vhost代理至18765、使用独立静态目录，回调精确location关闭access log；`nginx -t`通过。CRM A记录存在，无AAAA。
- **测试环境：**固定应用 `eefc490` 已部署，服务监听仍为 `127.0.0.1:18765`，`aluola_crm_dev` 迁移至 `0017`；正式业务库未访问。18888与CRM HTTPS均使用合成销售账号密码登录成功；销售目录/订单/报表、仓管库存/收货只读页面均返回200。CRM OAuth启动端点因CorpID/AgentID/Secret未配置返回503；没有真实企微OAuth。
- **Django/代理：**候选CRM host已加入 `ALLOWED_HOSTS` 与HTTPS `CSRF_TRUSTED_ORIGINS`；Django只信任Nginx覆写的 `X-Forwarded-Proto=https`。Django全局 `SESSION_COOKIE_SECURE=False`、`CSRF_COOKIE_SECURE=False` 未改，Cookie域保持host-only、SameSite=Lax；Nginx只在CRM HTTPS vhost使用`proxy_cookie_path / "/; Secure"`。HTTP 18888实测session/CSRF Secure=false；HTTPS CRM实测两Cookie Secure=true，均host-only、Path `/`、SameSite=Lax，且HTTPS密码POST的CSRF/session保持成功。

## 最小接入清单

1. **完成企业微信域名所有权校验。** `crm` A记录与专用HTTPS已可用；管理员提供企业微信原始域名校验文件及准确文件名后，在CRM vhost增加该文件的精确静态location，HTTPS GET核对原文件内容后提交校验，不伪造文件名或内容。
2. **配置真实企业微信应用材料。** 管理员需受控配置CorpID、AgentID、Secret及已批准的试点CRM用户绑定；配置前OAuth启动端点预期不可用，不做真实OAuth。
3. **保持回调与Cookie保护。** 全局Django Secure Cookie标志保持False以兼容HTTP 18888；仅CRM HTTPS vhost使用`proxy_cookie_path / "/; Secure"`。通过真实HTTPS CSRF登录已验证Secure、host-only、SameSite=Lax及session保持；HTTP 18888 Cookie标志仍为Secure=false。Nginx 1.18无`proxy_cookie_flags`，未升级。

### OAuth 回调日志风险

普通Nginx access log仍使用默认`combined`并记录完整`$request`，所以CRM OAuth回调精确location关闭access log。合成缺失state回调经严格HTTPS返回400并带`Cache-Control: no-store`、`Referrer-Policy: no-referrer`；唯一marker在Nginx access/error、CRM service journal、`crm.business`日志匹配数均为0。只验证本次合成错误路径；真实OAuth及上游失败路径待管理员凭据到位后验收，不读取其他日志原文。


## 企业微信后台配置

- 应用主页：`https://crm.onenesscyber.tech/accounts/wecom/login/?next=/`。该路径在同一HTTPS host启动OAuth并建立state/session；不可从IP:18888启动后跨host回调。当前HTTPS已验收，但因CorpID、AgentID、Secret尚未配置，该入口返回503，暂不能进行真实企微登录。
- 网页授权可信域名：填写 `crm.onenesscyber.tech`，不带协议头；OAuth `redirect_uri` 固定为 `https://crm.onenesscyber.tech/accounts/wecom/callback/`。启动和回调必须保持同一 HTTPS host。
- 仅配置已获批的少量试点成员可见范围。官方 `getuserinfo` 对企业成员即使不在应用可见范围内也可返回 `userid`，故服务端还必须只接受显式绑定的获批试点 CRM 用户；可见范围不是登录白名单。
- 首期采用 `snsapi_base`；只配置网页授权需要的接口权限，不开消息回调、通讯录同步或客户联系。`CorpID`/`AgentID` 和 Secret 分别由企业管理员通过受控渠道配置；Secret 不进入聊天、文档或 Git。

## 复用测试、备份和回退

- 仅使用测试数据库 `aluola_crm_dev`、开发服务和上游18765；固定版 `eefc490` 与迁移0016/0017已部署，`aluola_crm_business`未访问。CRM显式userid绑定/试点成员和真实OAuth仍待管理员材料与后续验收。
- 已为测试库及私有媒体完成全新配对备份，检查 `pg_restore --list`、gzip完整性、远端/本机散列与目标库身份；原运行代码包及静态资产亦留回退副本。凭据未复制或输出。
- 已按备份和固定制品 → 测试库迁移 → 独立vhost配置 → `nginx -t`/reload 的顺序执行；为CRM签发独立证书后已启用专用HTTPS vhost。原18888监听、UFW规则、根域及study站点未改。企微所有权验证材料尚未到位。
- 回退：删除新站点启用链接/文件并 `nginx -t`、reload；恢复旧测试 release 与配对的测试 DB/media 备份；DNS A 由域名管理员撤销，企业微信管理员将应用主页/可信域名恢复或停用试点应用。只回滚该新 vhost、测试库和试点配置，不触正式库、Hermes、共享 PostgreSQL 或原 18888。

## 2026-10-20 测试部署更新

- 固定包为 `eefc4900df1007a2191ff63d3577afdc681e45d1`，远端/本机归档 SHA-256：`06c653abbdd37be2fce146de1402640e8ca26e5f1d9949097a82124b161417fe`。原运行包 `71cf9f6` 的回退归档及 hash 保存在云端和本机私有部署目录。
- 停止测试服务后，为 `aluola_crm_dev/crmdev` 制作配对恢复点：`aluola_crm_dev_pre_0017.dump` SHA-256 `b958551662c8a6a7b92c3e776b8e4c38dce0482e320b7af5bc82ce924da604e5`；`private-media_pre_0017.tar.gz` SHA-256 `428328ce7d007407d52736e5aa1c895087b198af35a61f8a75bdfc2a80a33d8d`。文件均远端/本机相同、权限600；远端 `pg_restore --list` 与媒体 gzip 完整性检查通过。原静态目录的回退归档也保存在私有目录，新版静态收集到独立 `/var/www/aluola-crm-wecom/static`，未改原18888静态目录。
- 目标库身份断言通过后只迁移 `0016_cost_at_outflow` 和 `0017_accountprofile_wecom_userid`。Django `check`、`makemigrations --check --dry-run`、`migrate --check` 通过。测试服务active且只监听 `127.0.0.1:18765`。测试设置已将CRM域名加入`ALLOWED_HOSTS`/HTTPS CSRF来源，信任Nginx覆写的`X-Forwarded-Proto`，固定同域回调URL；CorpID、AgentID、Secret仍未配置。Django Secure Cookie标志保持False；HTTPS Secure由CRM vhost的`proxy_cookie_path`单独加。
- 18888及CRM HTTPS均使用私有合成销售账号真实密码登录；销售`/`、`/catalog/`、`/orders/`、`/reports/sales/`及仓管`/inventory/`、`/receiving/`只读GET均200。无业务POST。HTTP的`csrftoken`/`sessionid`为Secure=false、host-only、Path `/`、SameSite=Lax；HTTPS两cookie均Secure=true、host-only、Path `/`、SameSite=Lax。两入口session保持成功，HTTPS CSRF密码POST通过。
- 用户添加后DNS A已解析，无AAAA。共享证书SAN不含CRM，到期`2026-12-22 02:54:45 UTC`且未改；此前“共享证书静态匹配CRM”错误已更正。CRM独立证书SAN/checkhost通过，有效至`2027-01-18 UTC`；严格TLS真实域名curl返回200/验证0。CRM 443 vhost已启用，`nginx -t`及reload通过；根域/study/18888站点、UFW及Nginx全局未改。根域、study HTTPS也返回200/验证0。
- CRM vhost启用期间对精确回调发送非真实synthetic code/state，缺失state响应400并带no-store/no-referrer。按marker搜索Nginx access/error、CRM service journal及`crm.business`文件日志各命中0；未读取其他日志内容。仅覆盖此错误路径，不代表真实OAuth或所有异常类型已审计。
- 80端口外部合成HTTP-01 webroot探针通过，现有其他80 server_name仍为IP/root/study。独立证书lineage使用`authenticator=webroot`、`webroot_path=/var/www/letsencrypt`；`certbot.timer` active，未运行renew dry-run。无新增安全组来源。校验文件及CorpID/AgentID/Secret/试点绑定未提供；wecom登录启动返回503，真实OAuth未做。

## 需用户/管理员操作或实施授权

- 固定版测试部署、CRM专用证书及HTTPS入口均按授权完成。后续需企业微信管理员提供准确域名校验文件、设置自建应用主页/可信域名/试点可见成员，并经受控私有配置提供CorpID、AgentID、Secret和员工绑定；材料未到位前不做真实OAuth。
- 正式业务库仍不接入；企微材料配置后再做真实OAuth、试点身份映射/停用/权限验收。

## 限制与来源

未访问腾讯云控制台/安全组；没有改安全组或新增来源。Certbot为`crm.onenesscyber.tech`创建独立`webroot` lineage，续期配置指向`/var/www/letsencrypt`，`certbot.timer` active；没有运行`certbot renew --dry-run`。外网DNS严格TLS、H5登录、Cookie/CSRF、HTTP兼容均已验收，根域/study也返回200且TLS验证通过。真实企微OAuth及域名所有权校验文件尚未完成，因企微凭据/校验文件缺失；OAuth启动目前返回503。此前“共享证书静态匹配CRM”结论错误，已更正；共享根域证书未改。

- 企业微信可信域名与 OAuth：[开发者帮助 15372](https://open.work.weixin.qq.com/help2/pc/15372)、[域名所有权 FAQ 90457](https://developer.work.weixin.qq.com/document/path/90457)、[OAuth 授权 91022](https://developer.work.weixin.qq.com/document/path/91022)、[获取访问用户身份 91023](https://developer.work.weixin.qq.com/document/path/91023)、[获取 access_token 91039](https://developer.work.weixin.qq.com/document/path/91039)。查阅日：2026-10-20。
- Nginx：[HTTP 日志模块](https://nginx.org/en/docs/http/ngx_http_log_module.html)说明默认格式为 `combined` 且 `$request` 是完整原始请求行；[反向代理模块](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)说明 `proxy_cookie_path` 可在 server/location 使用、`proxy_cookie_flags` 从1.19.3才支持。查阅日：2026-10-20。

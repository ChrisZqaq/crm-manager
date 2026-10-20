# 企业微信 SSO 隔离测试基础设施方案

只读核验日期：2026-10-20。负责人依据用户直接确认记录：企业微信企业验证完成、用户有管理员权限，`onenesscyber.tech` 已备案；用户批准企业微信 H5+SSO 开发和 `crm.onenesscyber.tech` 作为候选，未授权 DNS、证书、Nginx、服务或公网配置变更。本报告不执行任何环境变更，不访问正式业务库，不读取/输出秘密或现有访问日志。

## 只读现状

- **DNS/外网：**只读查询显示 `onenesscyber.tech` 有 A 记录，`crm.onenesscyber.tech` 当前没有 A/AAAA 响应。`https://onenesscyber.tech/` 外网 GET 返回 200，TLS 校验成功；候选子域目前无法完成外网 HTTPS 请求。腾讯云安全组未通过 SSH 核验。
- **TLS：**当前 Nginx 所用证书静态主机名检查与 `crm.onenesscyber.tech` 匹配；根域外网 HTTPS 校验成功。证书有效至 **2026-12-22 02:54:45 UTC**。`certbot.timer` active，续期配置使用 Nginx authenticator；本轮未做续期演练。之前 WORK 中证书查询依赖错误属于早前记录；本次 `sudo certbot certificates` 只读查询成功，但不证明续期成功。
- **Nginx/入口：**Nginx 1.18.0 active，443 与 18888 监听；UFW 已允许两端口。443 现有站点为 `onenesscyber.tech` 与 `study.onenesscyber.tech`，没有 `crm.onenesscyber.tech` 的 server_name。可以另建独立站点配置，使用现有 443、现有证书及已有应用上游，不需新增端口或服务。原 18888 站点应保持原样。
- **测试环境：**云端 `aluola-crm-dev.service` active，Gunicorn 只监听 `127.0.0.1:18765`；Nginx 的 IP:18888 站点反向代理至该上游。登录页本机只读 GET：18765 和 18888 均返回 200。现有测试版本/数据库为 `71cf9f6`/迁移 `0015`/`aluola_crm_dev`；SSO 尚未在运行版实现。新验收后的代码需由后端固定版本后才可用于测试；正式业务库无 Web 入口，本方案不切正式库。
- **Django/代理：**只读核对当前测试服务加载的私密 settings overlay（不输出凭据）确认 `SESSION_COOKIE_SECURE=False`、`CSRF_COOKIE_SECURE=False`；`CSRF_TRUSTED_ORIGINS` 目前仅含旧 IP:18888、localhost:18888、127.0.0.1:18888，`DJANGO_ALLOWED_HOSTS` 不含候选 CRM 域名。overlay 未赋值 `SECURE_PROXY_SSL_HEADER`、`SESSION_COOKIE_DOMAIN`、`SECURE_SSL_REDIRECT`；因此 session Cookie 继续使用 Django 默认 host-only 域行为。现有 Nginx 代理会传 `Host`、`X-Forwarded-For` 和 `X-Forwarded-Proto`，但 Django 当前没有据此识别 HTTPS 的显式配置。不能对共享上游全局启用 Secure Cookie 或强制 HTTPS：这会改变仍通过 HTTP 使用的 18888 行为。仅 HTTPS vhost 的 Cookie 隔离尚未验证，需在固定版测试环境证实。

## 最小接入清单

1. **管理员准备 DNS 和企业微信校验文件。** DNS 管理员将 `crm` 子域 A 记录指向既有 CRM 主机公网地址；只有主机确有 IPv6 对外服务时才加 AAAA。用户/管理员从企业微信应用可信域名配置取得该域名专属校验文件。按官方要求将原文件放在 `https://crm.onenesscyber.tech/` 根路径可读的位置，精确返回文件内容、HTTPS GET 200 后再提交校验；校验通过后保留文件。DNS 新配置可能需要等待生效，不能因根域备案/解析正常就假定子域验证通过。
2. **经批准后新增单独的 HTTPS vhost。** 使用独立文件 `/etc/nginx/sites-available/aluola-crm-wecom.conf` 及对应启用链接；`server_name crm.onenesscyber.tech`，引用现有证书，校验文件用精确静态 location，CRM 路由代理到既有 `127.0.0.1:18765`，与原 18888 共用测试应用/数据库。复用现有 `Host`、`X-Forwarded-For`、`X-Forwarded-Proto $scheme` 代理头。候选子域的 Host 加入 `DJANGO_ALLOWED_HOSTS`；Django 代理安全配置只信任本机 Nginx 覆写的 scheme。应用 H5 登录表单需实际验证 CSRF、session、退出与权限。
3. **验证 HTTPS Cookie 隔离与回调日志。** 全局开启 Django Secure Cookie 会改变仍使用 HTTP 的 18888 行为。Nginx 1.18 没有 `proxy_cookie_flags`；`proxy_cookie_path` 受支持，但尚不能据此认定它能对本项目的 session/CSRF Set-Cookie 安全、正确地仅加 Secure。把它仅列为待验证候选：在固定版和隔离测试环境检查原始及浏览器收到的 Set-Cookie，并实测新 HTTPS 主机登录、CSRF、会话保持，同时确认 18888 的 HTTP 行为不变。若不能证明按主机隔离，停止实施并由负责人另行确定兼容方案；不升级 Nginx、不新增框架/服务/端口，不改全局 Cookie 设置。

### OAuth 回调日志风险

当前全局 Nginx `access_log /var/log/nginx/access.log;` 及现有站点日志未指定格式，按 Nginx 官方默认 `combined`。该格式含 `$request`，代表完整原始请求行；企业微信 OAuth 将 `code`、`state` 放在回调 URL 查询参数中，普通 access log 会记录完整请求行。没有读取任何日志内容。当前 CRM 测试 vhost 未单独声明 `error_log`，继承全局 `/var/log/nginx/error.log`；服务的 stdout 指向 systemd journal，stderr 为 inherit。仓库只配置 `crm.business` 私有文件日志（消息格式为时间/级别/消息）；当前 SSO 回调代码本身没有显式记录授权码、state 或完整 URL。上述配置不能证明 Django 请求异常、Gunicorn stderr/journal 或 Nginx error log 中不会出现查询值或带敏感值的异常文本，`access_log off` 也不能覆盖这些路径。尚未运行新版 SSO，也未查看任何日志内容。

最小处理：给 OAuth 回调使用专用精确 location，在 Nginx 关闭该 location 的 access log 或使用不含 `$request`、`$request_uri`、`$args`、`$query_string`、完整 Referer、`code`、`state`、token 的脱敏格式；同时核查并验证 Nginx error log、Django 错误处理、Gunicorn stderr/journal 均不记录查询串/秘密。应用只记录请求ID、认证阶段/结果和必要的内部用户ID，不记录回调全 URL；成功或失败后立即跳转到不带查询参数的干净 URL，并返回 `Cache-Control: no-store`、`Referrer-Policy: no-referrer`。后端固定实现后用合成的成功与失败回调验证全部日志出口，检查日志字段配置及合成值是否会泄露；不得用 `access_log off` 单独作为无泄露证明，也不读取或复制既有日志。

## 企业微信后台配置

- 应用主页：`https://crm.onenesscyber.tech/accounts/wecom/login/?next=/`。该路径启动 OAuth 并在同一 HTTPS host 建立 state/session；不可从 IP:18888 启动后再跨 host 回调，否则浏览器不会携带原 host 的 session Cookie，state 校验无法完成。
- 网页授权可信域名：填写 `crm.onenesscyber.tech`，不带协议头；OAuth `redirect_uri` 固定为 `https://crm.onenesscyber.tech/accounts/wecom/callback/`。启动和回调必须保持同一 HTTPS host。
- 仅配置已获批的少量试点成员可见范围。官方 `getuserinfo` 对企业成员即使不在应用可见范围内也可返回 `userid`，故服务端还必须只接受显式绑定的获批试点 CRM 用户；可见范围不是登录白名单。
- 首期采用 `snsapi_base`；只配置网页授权需要的接口权限，不开消息回调、通讯录同步或客户联系。`CorpID`/`AgentID` 和 Secret 分别由企业管理员通过受控渠道配置；Secret 不进入聊天、文档或 Git。

## 复用测试、备份和回退

- 使用既有测试数据库 `aluola_crm_dev`、专用开发服务和上游 18765；先等待后端固定 SSO 版本及独立代码验收。不部署 6024205 原候选（其不含 SSO），不访问或迁移 `aluola_crm_business`。确认目标连接名、角色、当前 migration 为 `0015` 后，再按新迁移计划只更新测试库。应用可见范围只放试点人，CRM 侧单独维护显式 WeCom userid 绑定；企微移除/离职需管理员同步停用 CRM 用户，并分别验证新登录拒绝和既有 session 撤权。
- 变更前为测试数据库和私有媒体做全新配对备份，检查 `pg_restore --list`、归档完整性、远端/本地散列及目标库身份；10-16 备份已过期，不可代替实施窗口的新恢复点。保存原固定 release 与独立 Nginx 文件的版本副本；不得复制/输出凭据。
- 上线顺序建议：备份和固定制品 → 配置独立 vhost/校验文件 location → `nginx -t` → 由管理员建立 DNS A → 等待解析并完成所有权校验 → 更新测试服务的应用设置/仅测试库迁移 → `nginx -t` 后 reload → 只读外网验证登录页与 HTTPS，再做获批的企微合成试点 OAuth 及权限验收。原 18888 监听、UFW 规则、原站点文件和服务不作改动。
- 回退：删除新站点启用链接/文件并 `nginx -t`、reload；恢复旧测试 release 与配对的测试 DB/media 备份；DNS A 由域名管理员撤销，企业微信管理员将应用主页/可信域名恢复或停用试点应用。只回滚该新 vhost、测试库和试点配置，不触正式库、Hermes、共享 PostgreSQL 或原 18888。

## 需用户/管理员操作或实施授权

- 当前用户已选 `crm.onenesscyber.tech` 候选并批准 H5+SSO 开发。仍须 DNS 管理员添加其 A 记录并提供企业微信产生的准确域名校验文件；本次未改 DNS。
- 需后端给出固定 OAuth 回调路径、User/Profile 绑定迁移和 session 撤权合同；负责人安排完成后再进入基础设施实施窗口。
- 企业微信管理员创建自建应用、下载校验文件、设置主页/可信域名/试点可见成员并在受控渠道配置凭证。员工试点名单与 CRM 对应测试账号须明确；CorpID/AgentID 可安全提供，Secret 不在聊天中交付。
- 基础设施操作前由负责人/用户另行确认具体的 DNS 记录值、vhost/Nginx reload、测试库备份/迁移和入口校验窗口。用户已批准 SSO 研发不等于已经批准这些线上基础设施变更。

## 限制与来源

本轮未访问腾讯云控制台/安全组；外网可访问性仅由根域 HTTPS 200 及本机 DNS 观察佐证。证书 SAN 主机名匹配不等于 `crm.onenesscyber.tech` 的 DNS、TLS 真实握手或 HTTPS 服务已验收。未执行证书续期演练、真实 OAuth、真实人员登录、测试库写入或任何服务变更。

- 企业微信可信域名与 OAuth：[开发者帮助 15372](https://open.work.weixin.qq.com/help2/pc/15372)、[域名所有权 FAQ 90457](https://developer.work.weixin.qq.com/document/path/90457)、[OAuth 授权 91022](https://developer.work.weixin.qq.com/document/path/91022)、[获取访问用户身份 91023](https://developer.work.weixin.qq.com/document/path/91023)、[获取 access_token 91039](https://developer.work.weixin.qq.com/document/path/91039)。查阅日：2026-10-20。
- Nginx：[HTTP 日志模块](https://nginx.org/en/docs/http/ngx_http_log_module.html)说明默认格式为 `combined` 且 `$request` 是完整原始请求行；[反向代理模块](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)说明 `proxy_cookie_path` 可在 server/location 使用、`proxy_cookie_flags` 从1.19.3才支持。查阅日：2026-10-20。

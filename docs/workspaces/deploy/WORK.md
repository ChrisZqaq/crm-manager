# 部署工程师当前任务

2026-10-08，任务DEPLOY-AUDIT-1：用户授权GPT-6 Luna通过本机 `ssh tx` 检查腾讯云服务器部署条件并向负责人报告。

状态：会话已创建，ID见公共协作登记；只读检查及负责人复核于2026-10-08完成，OS/Python探测已补充核实；后续未获部署执行任务。远端未安装或改动。

## DEPLOY-AUDIT-1 现场报告

观测时间：2026-10-08 13:51–13:52 +08:00。连接使用本机现有 `ssh tx` 别名和BatchMode；无密码交互。报告不记录公网IP、账号、域名或秘密。

**命令摘要**：远端 `hostnamectl`/`uname`/`lscpu`/`free`/`swapon`/`df -hT`/`df -i`/`uptime`/`vmstat 1 3`；systemd运行服务与定时器、`ss -lntup`、进程名/资源摘要；Python/数据库/代理/备份工具命令探测；`sudo -n -l`、UFW状态、Docker只读清单/资源和卷元数据、Hermes user systemd单元、证书到期元数据。未读取配置、环境变量、进程参数、密钥、数据库或业务文件。

复核补充命令：`cat /etc/os-release`（仅筛选版本字段）、`command -v`/`type -a`/`readlink -f`/`--version`核对python3、`/usr/bin/python3`及python3.12，`dpkg-query -W`查Python/nginx/certbot包版本，`head -n 1 /usr/bin/certbot`仅看shebang。确认实际系统Ubuntu 22.04.5；没有执行apt或安装操作。

**事实**：`/etc/os-release`确认Ubuntu 22.04.5 LTS（Jammy），不是此前候选信息中的24.04；Linux 5.15、x86_64、2 vCPU。RAM 1.9 GiB，总swap 2 GiB，采样使用约705 MiB swap，free约76 MiB、available约1.0 GiB。uptime 15天，load average 0.08/0.06/0.01；三秒采样总体空闲，单次出现短暂25% CPU且无持续swap进出。该瞬时available和历史swap占用不足以判定内存不足或够用；峰值负载未知。根ext4 50 GiB，已用26 GiB、可用22 GiB（55%）；inode使用14%。

Hermes以ubuntu用户进程运行，RSS约226 MiB；发现用户级 `hermes-gateway.service` active。当前服务另有Docker/containerd、nginx、SoYoung Operations Tracker和云监控/安全代理。Docker中有健康运行两周的tracker后端及MySQL 8.0，内存约64 MiB与57 MiB；MySQL数据使用Docker命名卷，后端有 `/app/data`、`/app/documents` 等绑定挂载。Docker监听映射仅在loopback 8000/3307；宿主另有loopback 18000。22/80/443绑定所有接口，NTP监听；没有看到PostgreSQL服务或 `psql`/`pg_dump`，也没有NAS/NFS/CIFS/SSHFS挂载。当前容器MySQL不是项目要求的PostgreSQL，不能视作可复用数据库。

默认PATH的 `python3` 和发行版 `/usr/bin/python3` 均指向 `/usr/bin/python3.10`，版本3.10.12；dpkg记录的Python 3.10包版本为3.10.12-1~22.04.18，Python元包3.10.6-1~22.04.1。`python3.12`命令不可用，dpkg未发现Python 3.12相关包；因此目前未发现满足项目Python >=3.12,<3.13的解释器。nginx为1.18.0-6ubuntu14.21，Docker 29.8，另有certbot、rsync/tar；没有psql/postgres/restic/borg。Certbot入口为`/usr/bin/certbot`，shebang使用`/usr/bin/python3`；timer启用，但直接运行 `certbot certificates` 因系统Python依赖错误（pyOpenSSL/lib不兼容，`GEN_EMAIL` 属性缺失）失败。只读证书元数据显示两个证书到期分别为2026-12-22、2026-12-28；此日期不证明自动续期可用，续期路径尚未验证。未读域名或证书内容。UFW active，默认deny incoming、allow outgoing，只允许入站22/80/443；腾讯云安全组无法由SSH确认。

登录用户在sudo组且 `sudo -n -l`显示免密全权限；普通用户不能访问Docker socket，元信息通过只读sudo命令取得。当前仅看见系统dpkg每日备份timer，没有项目数据库/附件备份单元、备份软件或外部备份挂载。NAS是否在线及异地传输条件未知。腾讯云控制台安全组、云盘快照策略、域名备案/DNS、公网可达性均未验证。

**条件性结论与风险**：机器规格与磁盘尚可支持进一步验证最小单机部署，但不能据短采样认定满足生产负载。现有瞬时内存和历史swap观测不能得出“资源不足”结论，峰值未知；部署前后仍需结合实际服务预算和运行观察判断CRM与共享服务能否共存。当前实际系统为Ubuntu 22.04.5，默认及发行版Python均为3.10，尚未发现项目要求的Python 3.12；不要把候选24.04信息当成现场事实。磁盘余22 GiB但未含新增附件及按30分钟、保留7天且服务器外副本的备份预算；目前未发现NAS挂载/项目备份，生产上线阻断项。证书尚未过期，但Certbot入口报错，续期能力没有验证。UFW规则不代表云安全组已正确开放。

最小建议：先由负责人确认项目是否仍选此主机及外部备份目标/NAS连接；若继续评估，只规划复用现有Ubuntu、nginx、systemd、rsync/tar能力，补足项目要求的Python版本、PostgreSQL及可验证异地备份/恢复方案；不预设容器化或升级。上线前需另行获得部署授权，解决Certbot检查故障并核对云侧网络规则、资源预算、备份与恢复验收。此报告没有开始部署。

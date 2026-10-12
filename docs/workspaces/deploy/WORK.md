# 部署工程师当前任务与交付状态

2026-10-08，任务DEPLOY-AUDIT-1：用户授权GPT-6 Luna通过本机 `ssh tx` 检查腾讯云服务器部署条件并向负责人报告。

状态：DEPLOY-DEV-ENV基线于2026-10-08完成；最新DEPLOY-IP-TEST配置、服务器内验证及本机无代理公网复验已通过。服务保持运行供用户测试；本轮其余远端变更已停止。下方DEPLOY-AUDIT-1、DEPLOY-PREP-PLAN及首次暂停记录均为历史状态。

## 历史：DEPLOY-AUDIT-1 现场报告

观测时间：2026-10-08 13:51–13:52 +08:00。连接使用本机现有 `ssh tx` 别名和BatchMode；无密码交互。报告不记录公网IP、账号、域名或秘密。

**命令摘要**：远端 `hostnamectl`/`uname`/`lscpu`/`free`/`swapon`/`df -hT`/`df -i`/`uptime`/`vmstat 1 3`；systemd运行服务与定时器、`ss -lntup`、进程名/资源摘要；Python/数据库/代理/备份工具命令探测；`sudo -n -l`、UFW状态、Docker只读清单/资源和卷元数据、Hermes user systemd单元、证书到期元数据。未读取配置、环境变量、进程参数、密钥、数据库或业务文件。

复核补充命令：`cat /etc/os-release`（仅筛选版本字段）、`command -v`/`type -a`/`readlink -f`/`--version`核对python3、`/usr/bin/python3`及python3.12，`dpkg-query -W`查Python/nginx/certbot包版本，`head -n 1 /usr/bin/certbot`仅看shebang。确认实际系统Ubuntu 22.04.5；没有执行apt或安装操作。

**事实**：`/etc/os-release`确认Ubuntu 22.04.5 LTS（Jammy），不是此前候选信息中的24.04；Linux 5.15、x86_64、2 vCPU。RAM 1.9 GiB，总swap 2 GiB，采样使用约705 MiB swap，free约76 MiB、available约1.0 GiB。uptime 15天，load average 0.08/0.06/0.01；三秒采样总体空闲，单次出现短暂25% CPU且无持续swap进出。该瞬时available和历史swap占用不足以判定内存不足或够用；峰值负载未知。根ext4 50 GiB，已用26 GiB、可用22 GiB（55%）；inode使用14%。

Hermes以ubuntu用户进程运行，RSS约226 MiB；发现用户级 `hermes-gateway.service` active。当前服务另有Docker/containerd、nginx、SoYoung Operations Tracker和云监控/安全代理。Docker中有健康运行两周的tracker后端及MySQL 8.0，内存约64 MiB与57 MiB；MySQL数据使用Docker命名卷，后端有 `/app/data`、`/app/documents` 等绑定挂载。Docker监听映射仅在loopback 8000/3307；宿主另有loopback 18000。22/80/443绑定所有接口，NTP监听；没有看到PostgreSQL服务或 `psql`/`pg_dump`，也没有NAS/NFS/CIFS/SSHFS挂载。当前容器MySQL不是项目要求的PostgreSQL，不能视作可复用数据库。

默认PATH的 `python3` 和发行版 `/usr/bin/python3` 均指向 `/usr/bin/python3.10`，版本3.10.12；dpkg记录的Python 3.10包版本为3.10.12-1~22.04.18，Python元包3.10.6-1~22.04.1。`python3.12`命令不可用，dpkg未发现Python 3.12相关包；因此目前未发现满足项目Python >=3.12,<3.13的解释器。nginx为1.18.0-6ubuntu14.21，Docker 29.8，另有certbot、rsync/tar；没有psql/postgres/restic/borg。Certbot入口为`/usr/bin/certbot`，shebang使用`/usr/bin/python3`；timer启用，但直接运行 `certbot certificates` 因系统Python依赖错误（pyOpenSSL/lib不兼容，`GEN_EMAIL` 属性缺失）失败。只读证书元数据显示两个证书到期分别为2026-12-22、2026-12-28；此日期不证明自动续期可用，续期路径尚未验证。未读域名或证书内容。UFW active，默认deny incoming、allow outgoing，只允许入站22/80/443；腾讯云安全组无法由SSH确认。

登录用户在sudo组且 `sudo -n -l`显示免密全权限；普通用户不能访问Docker socket，元信息通过只读sudo命令取得。当前仅看见系统dpkg每日备份timer，没有项目数据库/附件备份单元、备份软件或外部备份挂载。NAS是否在线及异地传输条件未知。腾讯云控制台安全组、云盘快照策略、域名备案/DNS、公网可达性均未验证。

**条件性结论与风险**：机器规格与磁盘尚可支持进一步验证最小单机部署，但不能据短采样认定满足生产负载。现有瞬时内存和历史swap观测不能得出“资源不足”结论，峰值未知；部署前后仍需结合实际服务预算和运行观察判断CRM与共享服务能否共存。当前实际系统为Ubuntu 22.04.5，默认及发行版Python均为3.10，尚未发现项目要求的Python 3.12；不要把候选24.04信息当成现场事实。磁盘余22 GiB但未含新增附件及按30分钟、保留7天且服务器外副本的备份预算；目前未发现NAS挂载/项目备份，生产上线阻断项。证书尚未过期，但Certbot入口报错，续期能力没有验证。UFW规则不代表云安全组已正确开放。

最小建议：先由负责人确认项目是否仍选此主机及外部备份目标/NAS连接；若继续评估，只规划复用现有Ubuntu、nginx、systemd、rsync/tar能力，补足项目要求的Python版本、PostgreSQL及可验证异地备份/恢复方案；不预设容器化或升级。上线前需另行获得部署授权，解决Certbot检查故障并核对云侧网络规则、资源预算、备份与恢复验收。此报告没有开始部署。

## 历史：DEPLOY-PREP-PLAN 云主机清理与环境准备方案（已被后续授权/执行记录取代）

**范围**：仅规划环境准备。推荐默认保留Hermes、tracker/MySQL、nginx及其全部现有数据；“清理”不等于重装系统、删容器/卷、卸载服务或替换Ubuntu。暂不配置CRM生产站点、导入真实期初或切换业务。本文中的命令和操作均为待审批步骤，未执行。

### 1. 保留项与清理候选

保留Ubuntu 22.04.5、SSH/云厂商代理、UFW、Docker/containerd、Hermes gateway、SoYoung tracker及MySQL、nginx、全部现存systemd单元、Docker volumes/bind mounts、证书与续期配置。当前未获这些服务的退役授权；可能承载业务的MySQL卷和 `/app/data`、`/app/documents` 一律按持久数据保护。

只读复核得到的候选与上限：APT归档缓存32 KiB；`/tmp`与`/var/tmp`共约172 KiB；Docker汇总显示4.2 MiB stopped-container可回收空间（仍须逐个确认归属）；systemd journal合计152 MiB（仅容量，未查明留存期限和诊断价值）。大部分候选都几乎没有收益；journal是唯一较明显部分，但必须先确认系统日志保留策略和诊断需求。因此推荐跳过“清理”操作，不为凑干净而删除任何对象。若之后确有需要，APT缓存仅在确认可重新获取后逐项考虑；临时文件只处理确认过期且未被进程使用的单项，不递归清空目录；journal仅按既有系统保留策略清理超期内容，策略未知先保留；stopped container须确认创建者/用途/无回滚依赖后逐个决定。Docker images共2.005 GiB且4个全为active，volumes共331.5 MiB且3个全标为active：全部保留，绝不运行`docker system prune`/`docker volume prune`，不删活跃日志、媒体或应用文件。其他系统/业务目录未测，不猜可释放量；不做全盘du或大范围扫描。

### 2. 快照、数据备份与回退

任何远端清理或环境写入前，先由用户/云管理员确认可用的整机磁盘快照、创建完成状态、保留期和恢复权限；快照目的地/费用/配额当前未知。若后续动作会触及tracker/MySQL或Hermes状态，再由现有服务责任人确认相应逻辑/文件备份及恢复方法；不读取或要求用户粘贴秘密。云内快照不能替代服务器外副本。NAS、正式域名与外部备份路径是正式上线事项，不是仅准备隔离Python/PostgreSQL并做本机合成健康检查的前置条件；未确认NAS前不挂载或配置传输，也不宣称项目已满足服务器外备份、每30分钟备份、最近24小时半小时点、7天每日点及RPO≤1小时要求。

环境准备回退以只禁用/移除本次新增的CRM独立systemd单元、nginx站点链接及独立目录为主；保留PostgreSQL数据目录和所有既有服务，除非单独批准数据销毁。整机快照仅作为严重故障恢复路径；如果tracker在快照后继续写入，不能直接回滚整机而丢失新数据，须先为既有业务再做新备份并安排停写窗口，恢复后复核tracker服务/数据库/附件。回退快照不视为CRM上线切换授权。

### 3. 隔离的CRM运行环境

待批准后只在Ubuntu现有版本上新增隔离的CRM运行时，不替换`/usr/bin/python3`或系统Python依赖。优先复用仓库已有`uv.lock`/uv工作流，由uv在专属位置提供Python 3.12并按锁文件建立项目venv；安装uv/下载解释器前先审核下载来源、校验和、体积与磁盘余量。若无法在不改系统Python/PPA/更换OS的前提下取得3.12，停止并报告，不升级发行版、不加未经批准的软件源、不源码编译。

建议CRM代码/venv使用独立应用目录，凭据只放权限受限的本地配置；新建专用低权限服务账号。PostgreSQL使用Ubuntu匹配发行版软件包及独立数据目录，仅Unix socket或loopback可达，不映射公网、不复用tracker MySQL容器/卷。CRM媒体持久目录与代码、日志分开，且备份纳入对象后才允许真实数据。应用由独立systemd单元启动，初始只配置1个web worker；nginx新增单独server block/配置文件和站点链接，保留现有站点及证书文件，先`nginx -t`再reload，不覆盖默认站点或共享全局设置。上游绑定选用预检确认空闲的loopback端口；当前已占loopback 8000/18000以及容器映射8000，不能直接猜端口。域名/证书未确认前只做本机健康检查，不开放新入口。

Certbot本轮只做诊断并列出独立、具体的修复步骤：保留现有入口、timer、证书与nginx站点；核对发行版包来源/版本一致性、模块导入路径和timer失败日志，确认它服务的域名/站点后，向负责人报告最小修复及影响。批准环境准备不包含Certbot修复、系统包重装/升级、修改system Python或既有证书；这些须单独审批。避免整体apt upgrade、直接覆盖证书、修改所有renewal配置或让新CRM复用未知的现有证书。涉及系统Python或既有证书变更时，须另给独立回退步骤和维护窗口，获单独批准后才能执行。证书到期时间不证明自动续期有效。

### 4. 资源预算与停止条件

现场观测：总RAM 1.9 GiB；一次available约1.0 GiB、历史swap使用约705 MiB；Hermes RSS约226 MiB，tracker后端/MySQL当时约64/57 MiB；CPU/内存峰值未知。这些观测既不证明内存不足，也不证明剩余足够。采用保守起步预算：CRM web先1 worker；PostgreSQL约256 MiB只是初始估算，非可保证的进程硬上限；CRM应用进程约256 MiB也是规划估值，CRM合计约512 MiB仅作预警预算（内核缓存/并发/查询峰值另计）。不为此增加额外资源管控框架。安装前若多次轻量采样中MemAvailable持续低于768 MiB或出现持续swap-in/out，先停并提交方案调整；试运行期若MemAvailable持续低于512 MiB、出现OOM/服务反复重启或持续swap-in/out，立即停止CRM验证并保留现有服务，报告负责人，不自动加swap/改限额/停其他服务。不得压测。

安装落盘预算暂设最多4 GiB增量（Python运行时、venv/依赖、PostgreSQL程序包、有限apt下载缓存及新日志），是保守预检边界，不是精确依赖体积承诺；下载前估算总体体积即可，不要求逐包精确值。若关键依赖总体体积仍无法估算、明显超过4 GiB，或安装后根盘可用空间预计低于18 GiB，则停止并报告，不扩盘、不清理Docker/业务数据。4 GiB预算不含生产数据库增长、媒体及异地备份；这些须另算并预留服务器外目的地。

### 5. 阶段与验收门槛

0) 用户确认快照创建/恢复责任人、可用性、完成状态、保留期和权限，保护现有服务后再写入。服务保留范围默认全部保留；环境准备不等待NAS或正式域名确认。域名与外部备份目的地留给正式上线门槛。若后续操作会触及tracker/Hermes业务数据，再先由相应责任人确认专门备份和恢复方式。
1) 复核CPU/RAM/磁盘、目标端口、已用空间分类、下载包体积、云安全组与权限；预检未满足上述余量或任何既有服务状态异常即停止。
2) 默认跳过清理；只有负责人确认存在具体过期对象且预期收益值得风险时，才对获批对象逐项处理并记录前后字节数。不得Docker prune或删除业务对象。量化验收：被批准项可用空间增量与估算相符、既有服务健康状态无变化；预计收益很小时跳过。
3) 环境准备：安装隔离Python3.12/CRM依赖与PostgreSQL，新建独立目录/服务/站点。验证系统Python版本及tracker容器/卷未变、PostgreSQL只能本机访问、CRM unit只读写约定路径、nginx语法成功、现有网站与tracker探针正常、CRM不占用已有端口、根盘余量≥18 GiB；任一项失败立即停CRM新增服务并回退新增nginx链接/unit，不碰既有服务。
4) 运行环境交付后停在合成数据健康检查，不导入真实期初、不开放正式业务、不让真实员工写入。之后正式上线是单独授权阶段：需项目QA验收、负责人验收、域名/安全组/备案确认、真实期初与权限确认、外部备份+真实恢复演练及切换窗口。环境准备通过不等于生产就绪。

**重启**：方案不包含整机重启或已有业务服务重启。安装依赖、建venv、启动新PG/CRM单元及nginx受控reload原则上可不重启主机；如果实际工具要求内核/系统重启，立即停止并提前通知用户说明原因、影响、窗口和回退方式，收到单独批准后才能安排。任何情况下不擅自重启Hermes、tracker、nginx或整机。

### 6. 需要用户决定

1. 默认是否保留并继续运行Hermes与SoYoung tracker/MySQL全部数据？若考虑退役，请分别明确服务及其数据的最终保留/迁移责任人；未答复按完整保留。
2. CRM使用哪个已备案/可解析的专用域名或子域名，DNS/腾讯云安全组由谁管理？未确认前仅本机验证。
3. 服务器外备份目标选NAS还是其他已批准位置？NAS的持续在线、可用空间、连接方式及凭据保管方式由负责人另行核对；不在聊天中索取/粘贴密码。未确认前不得挂载，也不得宣称生产备份就绪。

**历史决定**：本方案曾待审批；后续用户明确授权的执行范围及实际结果见下方DEPLOY-DEV-ENV记录。

## 历史：DEPLOY-DEV-ENV 首次暂停记录（2026-10-08）

用户明确授权 tracker/Docker 有序关停并保留全部对象，再搭隔离开发环境、暂不上线；允许环境任务需要的远端文件。遵守不得绕过网络限制、不动Hermes/无关业务、不得删除容器/镜像/卷、不改开机策略、不得重启服务器。

**连接与范围**：本机 `ssh tx` BatchMode当前成功，无需规避IP/网络限制。再次清点全部4个Docker容器：都带compose project `soyoung-data-tracker`，没有其他项目容器；backend/MySQL为运行状态，frontend/Redis原已退出约8周。宿主`soyoung-new.service`描述为SoYoung Operations Tracker，active/enabled；Hermes user gateway active。Docker.service/socket/containerd原均active/enabled。容器ID、镜像、状态、挂载、restart policy及unit状态已存私有本地文件[pre-stop-20261008.txt](/Users/chrisz/Documents/projects/aluolaCRM/.local/deploy-dev-env/pre-stop-20261008.txt)，权限700目录/600文件，不含秘密。

**数据保护门槛**：本次SSH检查未发现快照/备份timer或NAS挂载；腾讯云控制台快照、完成状态及恢复权限无法经SSH验证。依用户指示，仅执行保对象有序关停；在快照/必要保护确认前停止后续清理和系统包安装。所有容器、镜像、卷、bind数据保留；未删或清理文件，未改任何服务enabled/restart policy。

**关停结果**：依序停止`soyoung-new.service`、`soy-tracker-backend`、`soy-tracker-mysql`、`docker.socket`、`docker.service`。frontend/Redis原已停止且未启动。停后host tracker、Docker socket和daemon inactive，但保持enabled；containerd.service为避免超出需要保持active/enabled，`ctr -n moby tasks list`为空。Docker socket当前inactive，Docker API只读查询返回daemon未运行，不能经socket重新启动dockerd。`soyoung-new.service`、`docker.service`和`docker.socket`均仍enabled，因此主机重启后宿主tracker、Docker服务/socket可能自动启动；容器`unless-stopped`策略也未变。没有为确保持续关停而禁用或mask任何服务。重启由用户安排，未执行。

停后Hermes gateway和进程仍active；无uvicorn/mysqld/redis-server/docker-proxy/dockerd进程，原loopback 8000/3307/18000端口已释放，22/80/443仍监听。root盘仍50 GiB、余22 GiB；RAM available约1.1 GiB、swap使用约217 MiB。SSH连接成功，未确认是否有云侧IP限制；没有采取绕过操作。

**历史暂停状态**：该记录反映用户确认快照前的中间状态。此后用户确认已创建快照并要求开始执行，结果见下方。

## 当前交付：DEPLOY-DEV-ENV 执行结果（2026-10-08）

恢复依据：负责人转达用户最新确认“我已自行创建了实例快照了，开始执行”；依指示不索取快照编号。恢复前复核host tracker unit/Docker service/socket均inactive，Hermes active，根盘余22 GiB、RAM available约1.1 GiB、swap约217 MiB。Docker对象均保留。用户授权继续环境准备，不上线。

**PostgreSQL安装**：Jammy仓库候选PostgreSQL14.24-0ubuntu0.22.04.1，8个新包、42.0 MB下载、160 MB预计新增占用，0升级；APT未执行update或upgrade。安装新建cluster 14/main，原有nginx/SSH仍active，SSH会话持续。needrestart输出“Pending kernel upgrade”（运行5.15.0-191、包状态提示5.15.0-198）及若干服务建议；我未主动重启任何既有服务/主机，也未处理内核提示。复核时nginx PID仍964、SSH仍active，近15分钟两者无stop/start/reload日志，tracker与Docker保持inactive。其他被列服务未逐个做安装前后PID基线比较；不据此声称全部服务均未被包钩子触碰。主机重启未执行，当前PostgreSQL/CRM无需为本次安装而重启主机。

**隔离代码与解释器**：确认`d859839..df63f34`的`manage.py/config/crm/pyproject.toml/uv.lock`无应用差异，工作树上述应用文件干净。由本机`git archive df63f34`只传这些路径（71个文件、约1.4 MiB），未传Git历史、`.local`、表格/文档、凭据或真实数据。路径`/opt/aluola-crm-dev/app`归专用nologin系统用户`crmdev`；venv在同目录`.venv`，uv-managed CPython3.12.13位于`/var/lib/aluola-crm-dev/python`并使用`--no-bin`，系统`/usr/bin/python3`保持3.10.12。

**锁依赖与下载处理**：服务器已有uv0.11.32。服务器在线`uv sync --locked`两次长时间停滞；第一次约6分钟后终止唯一自有uv进程，第二次用同官方源`UV_HTTP_TIMEOUT=60`、`UV_HTTP_RETRIES=1`，约184秒无输出后终止。没有收到明确拒绝/封禁信息，不据此称服务器IP被封。官方PyPI simple index未列锁定的Pillow12.3.0（列至12.2.0）；从uv.lock所指`files.pythonhosted.org`下载精确manylinux CPython3.12 wheel并核验SHA-256与lock相同。随后本机用`uv export --locked --format requirements.txt --no-dev --no-emit-project`生成带hash清单，pip以Linux x86_64/CPython3.12/manylinux_2_28等目标、`--only-binary`、`--require-hashes`从官方PyPI下载9个锁定wheels。经SSH传输后的9个wheel再次逐一SHA256验证通过，再在服务器以`uv pip install --offline --no-index --find-links ... --require-hashes`安装；无编译、无版本改动。任务自有本地pip工具/轮子、远端wheel暂存和uv包缓存已清理；保留私有wheel hash记录[`.local/deploy-dev-env/wheels.sha256`](/Users/chrisz/Documents/projects/aluolaCRM/.local/deploy-dev-env/wheels.sha256)及关停恢复元信息。

**开发数据库/本机服务**：PostgreSQL使用发行版默认cluster，`listen_addresses=localhost`、port5432、Unix socket `/var/run/postgresql`；监听只见127.0.0.1:5432，不开放公网。创建无密码、peer认证的系统/数据库用户`crmdev`和空数据库`aluola_crm_dev`；Django `auth_user`计数为0，无真实账号/期初数据。开发环境变量及媒体/日志目录在`app/.local`，环境配置600、媒体和日志目录700，不含真实凭据。Django独立systemd unit `aluola-crm-dev.service`以`crmdev`运行`runserver --noreload 127.0.0.1:18765`，未enable开机启动。未改nginx/Certbot、未新增公网规则、未重启主机或既有服务。

**启动/停止与访问**：本机转发后访问`http://127.0.0.1:18765/`；远端仅监听loopback。启动CRM：`sudo systemctl start aluola-crm-dev.service`；停止：`sudo systemctl stop aluola-crm-dev.service`；状态：`sudo systemctl status aluola-crm-dev.service`。服务当前active、disabled（未设开机启动）。数据库服务为`postgresql.service`，当前active；配置文件为`/opt/aluola-crm-dev/app/.local/config/dev.env`（600），私有媒体/日志目录为`/opt/aluola-crm-dev/app/.local/private-media`和`.../.local/logs`（700），拥有者均为专用用户`crmdev`。数据库Unix socket peer登录，用户名/数据库均为`crmdev`/`aluola_crm_dev`，无密码凭据。

**验收**：远端Python 3.12.13；锁定9依赖安装，`uv pip check`通过；`manage.py check`返回“no issues”；迁移应用成功（CRM 0001–0013及Django auth/admin/session migrations），`migrate --check`成功；Unix socket连接身份`crmdev@aluola_crm_dev`；`/accounts/login/`本机HTTP 200；`ss`仅显示开发端口127.0.0.1:18765与PostgreSQL 127.0.0.1:5432。Dev unit与PostgreSQL active，dev unit disabled（不随启动自动开启）。Hermes active；`soyoung-new.service`/`docker.socket`/`docker.service` inactive；containerd保留active。数据库和开发服务不会触及Docker volumes。最终根盘余22 GiB/50 GiB；available RAM约1.0 GiB、swap约220 MiB；dev unit当时约47.7 MiB。单次资源值不代表峰值容量。

**保留风险/恢复**：旧tracker host unit、Docker service/socket及容器restart policy按原enabled/unless-stopped策略保留。主机重启后host tracker及Docker/socket可能自动启动；未disable/mask。Docker容器与原MySQL数据仍在，手工stop前metadata/ID见私有本地恢复记录；如需恢复tracker，先通知负责人/用户，然后按metadata依次启动Docker daemon/socket、MySQL与backend以及host unit，核验tracker，再确认Hermes未受影响。开发环境停止/回退只需`systemctl stop aluola-crm-dev.service`；不删除合成数据库、Python或原持久数据。无NAS/正式域名/生产HTTPS/真实期初；正式上线另行授权。

## 当前任务：DEPLOY-IP-TEST IP访问合成测试环境（2026-10-08）

**授权/基线/范围**：用户直接授权将固定基线`df63f34325056a9ce06eac0da92c61d216d60f6e`（应用版本`d859839`）部署为IP＋端口测试环境；不改CRM业务代码及`uv.lock`，不导入真实期初、客户、收款凭证，不做业务切换；保留Hermes及tracker/Docker停机、不清数据、不重启主机。按现有固定角色普通权限建立合成测试账号和最小订单依赖。公网安全组无法经SSH管理，按下文留给用户操作。

**远端路径/服务**：代码仍位于`/opt/aluola-crm-dev/app`，运行用户`crmdev`。`aluola-crm-dev.service`通过root-owned drop-in `/etc/systemd/system/aluola-crm-dev.service.d/iptest.conf`以Gunicorn 26.2.0单worker替换runserver，后端只绑定`127.0.0.1:18765`；当前active、disabled（不开机自启）。依赖从官方PyPI取得的`gunicorn-26.2.0-py3-none-any.whl`按SHA-256 `bd249d0b3f7972f7432f0a6b6ff3b3ee2d129f70cd1ff6c09a9dd9e29a2b88e3`核验后离线安装，`uv pip check`检查10项兼容；核心锁文件未改。版本和wheel hash记录在私有本机`.local/deploy-ip-test/requirements.txt`及`wheelhouse.sha256`。官方发布记录：[PyPI Gunicorn 26.2.0](https://pypi.org/project/gunicorn/26.2.0/)。

Nginx新增独立配置`/etc/nginx/sites-available/aluola-crm-iptest.conf`及启用链接`/etc/nginx/sites-enabled/aluola-crm-iptest.conf`，只代理18888至loopback 18765；`nginx -t`成功后受控reload，原站点配置未改。`/static/`唯一映射到`/var/www/aluola-crm-iptest/static`，由`collectstatic`收集130个静态文件；`/media/`固定404且没有静态映射。私有媒体仍在`/opt/aluola-crm-dev/app/.local/private-media`（700）；配置在`.local/config/dev.env`、`deploy_settings.py`（600），运行日志目录700。`DJANGO_DEBUG=0`、强随机secret、`ALLOWED_HOSTS`仅服务器IP/localhost/127.0.0.1，CSRF来源限同一IP/loopback的18888 HTTP；HTTP测试的session/CSRF secure cookie关闭。服务器监听18888的IPv4/IPv6；UFW新增18888/tcp入站规则（其余端口未新增），PostgreSQL仍仅127.0.0.1:5432。

最终观测服务状态：nginx、PostgreSQL、`aluola-crm-dev.service` active；该CRM unit disabled。Hermes的ubuntu用户级`hermes-gateway.service` active；`soyoung-new.service`、`docker.service`、`docker.socket` inactive但原enabled策略未改，containerd active。原nginx站点文件与基础CRM unit的部署前后SHA-256相同。部署时根盘50 GiB剩22 GiB，MemAvailable约1078 MiB、swap占用约213 MiB，CRM service当时约58.9 MB；这些是单次观测，不代表峰值或容量保证。没有重启主机或既有服务；旧needrestart待处理内核提示未处理。

**合成测试资料**：数据库原有用户/业务数据均为空。创建老板、销售、仓管／财务三个非staff、非superuser账号，并加入项目固定默认角色组。账号名、每账号独立随机强密码和IP测试URL仅保存在本机[`.local/deploy-ip-test/credentials.json`](/Users/chrisz/Documents/projects/aluolaCRM/.local/deploy-ip-test/credentials.json)，目录700、文件600且已被Git忽略；不抄写到本文或公共协作记录。合成业务数据为测试IP/TST-0001商品、合成仓库、供货方、线上渠道、对公测试账户；20盒以100.000单位成本通过现有采购预览/确认服务入库，生成一条业务流水。没有客户/真实银行账号/凭证图片或真实业务数据；未创建销售订单。保留项目正式期初导入开关，不用期初流程写测试库存。

**服务器内验收**：`manage.py check`无问题；`collectstatic`复制130文件；`uv pip check`通过，Gunicorn 26.2.0、单worker服务active。Django设置核验DEBUG关闭、Host与CSRF allowlist精确、`/static/`和专用STATIC_ROOT正确。服务器本机经nginx GET登录200、静态200、在私有媒体目录存在合成探针期间`/media/...`为404、未批准Host为400。Django请求客户端在enforce-CSRF模式下验证正确登录/错误密码、匿名订单页跳转；老板的收货/下单页均200，仓管收货200/下单403，销售下单200/收货403；销售能读到合成SKU、仓库、渠道、收款账户和库存API。库存余额20、成本2000.000、仅一条合成采购流水。临时私有媒体探针与含密码验证脚本已清理。

**公网复验**：用户确认腾讯云安全组已允许当前登录IP后，从本机运行curl并显式`--noproxy '*'`直连凭据文件中的地址；登录页GET 200、销售测试账号正确登录并跳转目录页、`/static/admin/css/base.css` 200、私有媒体路径404、未批准Host 400。公网复验成功，没有改变安全组/UFW来源、扩大来源或再次操作服务器。SSH端口转发仍被现有sshd策略拒绝；未绕过或修改。精确测试URL及账号仍只保存在私有本机credentials文件。此入口为HTTP无TLS，仅使用随机测试账号和合成数据，不放真实客户/收款凭证；应用、数据库、SSH未新增公网监听。未执行完整订单/收款业务链、全套QA或正式验收，不等于生产上线。

**操作/回退**：检查、启动、停止测试服务分别用`sudo systemctl status aluola-crm-dev.service`、`sudo systemctl start aluola-crm-dev.service`、`sudo systemctl stop aluola-crm-dev.service`；当前active但disabled，重启主机后不会自动启动，且主机重启可能恢复保留enabled的tracker/Docker，本轮未重启。撤回入口时先`sudo unlink /etc/nginx/sites-enabled/aluola-crm-iptest.conf`、执行`sudo nginx -t`再`sudo systemctl reload nginx`，并删除UFW 18888/tcp规则；若恢复原开发配置，将`dev.env`中的`DJANGO_DEBUG`改回1、`DJANGO_ALLOWED_HOSTS`改回localhost/127.0.0.1、`DJANGO_SETTINGS_MODULE`改回`config.settings`并移除本轮`PYTHONPATH`项；之后`sudo unlink /etc/systemd/system/aluola-crm-dev.service.d/iptest.conf`、`sudo systemctl daemon-reload`并重启unit可恢复基础runserver命令。随机`DJANGO_SECRET_KEY`可继续保留，不影响本机开发回退。不要删除合成收货流水或历史对象；如需清除合成数据库另行明确授权和核对影响。当前服务继续供用户测试；服务器后续变更及外部复验待负责人安排。

## UI-USABILITY-FIX 固定版本部署准备（2026-10-09）

固定交付 `51f5d8ff9c193620c94c9651b3937b5e2e826d50` 相对远端应用基线 `d859839` 仅涉及9个CRM静态/模板文件：CSS、订单/收款相关模板及少量列表模板；无模型、迁移、依赖、配置或API文件变更。模板含页面交互脚本，因此需整体更新模板与静态文件；无需数据库迁移或扩展网络入口。

执行门槛：等待QA独立功能/并发验收、UI实际视觉复查通过及负责人复核。额度恢复或部署准备不构成发布授权；门槛未满足前不传文件、不改远端、不重启服务。

门槛满足并获发布安排后，最小步骤为：核对远端当前提交仍为已验收的 `d859839`，保留当前发布文件副本及提交标识；仅部署固定 `51f5d8f` 的CRM应用文件，不覆盖远端 `.local` 配置/媒体/日志；运行 `collectstatic --noinput` 更新隔离的静态目录；检查服务配置与应用健康后重启 `aluola-crm-dev.service` 使模板缓存更新，并复核登录页、静态资源及验收指定的关键交互。继续保留Nginx 18888、UFW来源限制、合成数据、Hermes和tracker/Docker状态，不做主机重启。

回退：若发布后关键验收失败，将应用文件恢复到发布前保存的 `d859839` 版本，重新 `collectstatic --noinput` 并重启同一CRM unit，再核对服务和登录页；不回滚数据库、不删除合成订单/收款流水、不改Nginx/UFW。若无法确认发布前文件副本或当前远端基线与预期不符，停止回退写入并上报负责人，避免覆盖期间产生的其他变更。

## 后续版本发布准备（2026-10-09）

负责人新派发顺序为后端固定交付→QA→前端→QA/UI→负责人最终验收。当前共享HEAD处于后端写入窗口，不能从它分发或部署；已验收IP测试环境仍是 `d859839`，`51f5d8f`只是旧UI-USABILITY-FIX版本，不能代替本轮最终版本。QA报告权限缺陷，当前未通过；等缺陷修复后重新固定/验收、负责人提供完整SHA及明确执行消息，才进入发布。

发布前检查清单（只准备，不执行）：

1. **固定输入**：核实最终完整SHA、其父版本、仅包含已验收文件/迁移的变更清单；本机从该SHA导出固定归档并记录SHA-256。远端先只读确认当前部署版本、CRM/Nginx/PostgreSQL健康及可用磁盘；版本不符或工作树/迁移范围含糊即停止。
2. **数据库备份**：仅当固定版本包含数据库迁移或其他数据结构/内容写入时，在任何传输和迁移前备份云端合成测试库；用权限受限的私有临时位置保存，记录数据库名、时间和哈希，并用 `pg_restore --list` 验证备份可读。对0014，QA已实测反向迁移只恢复生日/提醒列结构：既有生日会变NULL、提醒默认值变为True，新客户/订单开票字段被删除，之后前向迁移不能恢复原值；语言迁移的reverse函数也不代表整批迁移可无损回退。因此必须先完成且验证迁移前备份，并安排维护窗口暂停该环境的所有写入。没有迁移/数据写入时，不为静态或模板改动制造数据库副本。正式数据不在本环境范围内。
3. **固定版本分发**：通过现有SSH通道传输上述SHA导出的归档，远端校验归档哈希后先展开到临时发布目录；只部署已确认的应用文件及迁移，不覆盖 `.local` 配置、媒体、日志或测试数据库文件，不读取或回显凭据。禁止直接同步共享HEAD或整个工作区。
4. **迁移与切换**：有迁移时先审阅SQL/操作的锁表及数据影响，在已验证备份和负责人安排的维护暂停写入窗口内，按最终固定版本执行一次；失败立即停止后续切换并保留错误信息。无迁移则跳过数据库命令。切换后运行 `collectstatic --noinput`，确认静态目录权限/资源；重启仅 `aluola-crm-dev.service` 使模板/应用代码生效，不reload Nginx、不重启主机。按负责人指定的冒烟项核对服务状态、登录页、静态文件和目标交互。
5. **回退判定**：代码回退只恢复应用文件，不能恢复被迁移改写或删除的数据。0014明确不得把“仅回旧代码”或“执行reverse migration”当作无损回退；恢复旧数据必须使用迁移前备份，并由负责人事先明确是否及如何恢复、维护暂停写入的起止和备份后数据处置。整库恢复会丢掉备份后测试写入；未获负责人明确恢复安排时停止在写入前并上报。保留合成业务流水，除非负责人另行明确清理范围。

固定SHA、迁移风险、备份结果及负责人发布消息缺一时均不执行远端写入。入口18888及来源限制保持不变；Hermes继续运行，tracker/Docker保持当前停机状态，不新增端口/域名/来源规则，不导入真实期初，不重启主机。

## USER-REVISION 发布准备复核（2026-10-09）

**发布状态**：后端 `694aaad08dfaa12b92bf3e18360edf686b588f92` 已独立验收且无迁移；前端 `febc104` 固定版未包含随后批准的登录页，当前前端仅有最小登录页写入窗口，之后QA/UI统一验收。现阶段明确暂不部署；最终完整SHA和负责人执行消息尚未提供。只读查库及发布准备不构成部署/迁移授权。

**迁移前语言阻断检查**：通过 `ssh tx` 以 `crmdev` 的Django ORM对现有云端合成库作只读聚合；0014未应用。唯一 `Product.language` 旧值为`(空字符串, merchandise)=1`；必填类型（`booster`/`gift_set`）总数0、必填类型空语言数0。没有其他语言名称可供映射，也没有必填类型缺失语言；该记录是可选语言的周边商品，不触发0014中必填语言阻断。没有输出商品名/ID、客户、库存、成本、凭证或凭据，未更改数据库或服务。

**现有备份/恢复路径**：不满足0014发布要求。部署审计记录未发现项目数据库备份单元/工具或外部存储路径；规格中的每30分钟数据库及凭证文件备份、保留24小时半小时点和7天日点、服务器外副本及实际恢复验证目前未实施。用户先前确认的实例快照发生在CRM PostgreSQL数据库/测试数据建立之前，且没有记录对该快照的恢复演练，因此不能作为0014迁移前数据恢复点。云端快照/控制台状态本轮不可读确认。0014发布仍须另行安排迁移前一致性数据库备份并验证可读性/恢复办法、暂停写入和负责人明确的失败恢复及备份后数据处置；记录时尚未创建迁移备份，实际授权的本轮预检结果见下节。

## DEPLOY-IP-TEST 迁移前备份/恢复预检（2026-10-09）

依据负责人明确授权，对现有合成测试库 `aluola_crm_dev` 创建一次一致性 `pg_dump --format=custom`，通过SSH流式保存至被Git忽略的本任务私有目录 `.local/deploy-iptest-backup-verify-20261009/`（目录700）。文件 `aluola_crm_dev_pre0014_20261009T184754+0800.dump` 完成于本机时间2026-10-09 18:48:01 +0800，权限600，大小214475字节，SHA-256 `7264f927873ddc6ad922a35c98defbc8f2ebec34907fa147a34e1288bdb647a0`。远端 `pg_restore --list` 通过标准输入读取归档成功；本机未安装pg_restore，因此未在本机运行。备份内容不显示或提交，可能包含合成账号凭证散列，继续仅存私有目录。

先确认目标库 `aluola_crm_backup_verify_20261009_01` 不存在，再以源库UTF8/en_US.UTF-8 locale新建并成功恢复。首轮原始列摘要比较未通过，后续按table/column名稳定排序复核，详见下节。迁移记录数31、商品总数1匹配；没有查询客户内容、库存/成本、凭证或密码。按授权仅删除本次创建的验证库，之后确认目标库不存在；源库、应用、服务未改。

后续最小诊断：原始摘要仅查public的information_schema.columns，字段为table/column名、ordinal_position、data_type、udt_name、is_nullable和column_default；ORDER BY `table_name, ordinal_position`，包括owner与否为否（没有查询owner），且没有包含constraints/indexes/precision。依授权另建唯一隔离库 `aluola_crm_backup_verify_logical_20261009_01`，用同一备份恢复成功。稳定的逻辑元数据复核覆盖关系/表、396列（含类型、长度/精度、null、default、identity/generated）、列相对顺序、286约束、186索引、45序列及视图。46表、全部上述结构项目和列相对顺序相符；只读计数 `django_migrations=31`、`crm_product=1` 相同。原始9个`ordinal_position`与恢复后物理位置不同；源结构有9个非连续位置，恢复库没有，列名相对顺序0张表改变，这是物理attribute slot空洞被CREATE/RESTORE压紧所致，不是逻辑列差异。另15条约束的`pg_get_constraintdef`呈现文本不同，仅varchar到text的数组cast从数组尾部cast改为元素cast写法；cast归一后15条定义均相等，约束名、类型、验证状态相同。类型/长度/精度/null/default、列顺序、索引和非约束结构无差异。未比较owner；本轮恢复为postgres角色，不能据此证明所有权与源库相同。

更新结论：该一致性pg_dump在本次隔离恢复中的逻辑schema/表比较及限定行数核对通过；首轮不一致是物理列ordinal数值及PostgreSQL约束表达式格式造成的摘要误报。预检库已按授权清理并确认不存在。该预检只证明此次合成库归档可恢复到逻辑结构匹配，未做完整应用/业务恢复演练、owner/权限还原、外部副本或定时备份验证；不能替代0014前最新备份、暂停写入和负责人明确的数据恢复/备份后写入处置。

## 当前IP测试环境更新发布清单（2026-10-09，只准备）

**当前状态**：负责人最新安排为统计后端→前端→独立QA/UI→负责人最终验收，再更新本机及云端IP测试入口。工作HEAD `37bd181` 不是发布版本；云端仍运行旧应用版本 `d859839`。当前没有最终完整发布SHA、最终整体验收或执行指令；本节只作操作清单，不部署、不迁移。

**最小发布步骤**

1. **锁定制品**：等负责人给最终完整SHA及执行消息，确认该SHA已通过QA/UI和负责人复核；基于该SHA核对相对云端/本机各自当前版本的代码、迁移、依赖和静态资源清单，从固定SHA生成归档并记录SHA-256。只传应用代码、迁移、已批准静态资产；不传/覆盖 `.local`、`dev.env`、登录测试凭据、媒体、日志、venv、缓存。两套测试环境分开更新，不互相复制数据库；保存各自回退所需的旧版本标识/制品。哈希不一致或范围外改动即停止。
2. **安排停写**：本机和云端分别确认维护窗口、用户停止操作，确认没有并发写入；只在负责人明确执行后停止相应CRM应用服务，等待在途请求结束并只读确认无CRM写事务。Nginx、18888/UFW及来源限制不变；不停止Hermes，不启动tracker/Docker，不重启主机。不得在运行版本继续可写时制作迁移备份。
3. **复查迁移门槛**：只依据最终SHA审阅迁移依赖与实际diff；若含0014，在停写后重新按语言名称/类型聚合 `Product.language` 并检查补充包/礼盒必填空值。允许映射仅按已批准四种语言；出现未知名称或必填类型缺失时停止并报负责人，不自动映射。此前预检仅看到一个空语言`merchandise`、必填空值0且0014未应用，不能代替发布窗口的复查。0014移除生日/提醒字段并变更开票字段，反向迁移不保留这些数据；不把代码回退或reverse migration当无损恢复。
4. **最新备份**：对每个即将迁移的环境分别备份其当前数据库。停写后使用自定义格式 `pg_dump -Fc`，记录源库、UTC/本地时间、大小、SHA-256和权限，`pg_restore --list`通过后才继续；最终恢复包不要使用 `--no-owner`/`--no-acl`，否则不能验证对象所有者/ACL。现存预检包创建于更早时点，且生成时省略owner/ACL，仅供此次逻辑结构对照，不可复用作上线恢复点。若该环境有需要保留或被数据库引用的私有上传文件，同一停写窗口将私有媒体作为不展开内容的归档备份并记录大小/hash/权限；不把MEDIA并入STATIC_ROOT。若不能安全恢复相关owner/ACL或媒体关联，暂停并报告。
5. **迁移与更新**：在应用保持停写时，将验过哈希的固定版本部署到该环境，不覆盖配置/凭证；确认发布代码/依赖与迁移文件完整后按一次性窗口执行迁移。随后 `collectstatic --noinput` 更新独立静态目录，确认私有媒体仍不可经 `/media/` 公开，再启动仅对应的CRM unit。不开新端口、不改域名、Nginx、安全组/UFW或CSRF/Host来源。只有CRM服务需重启；不用重启Nginx或主机。
6. **健康与验收**：部署前只读记录当前版本、CRM/Nginx/PostgreSQL状态、磁盘可用量及CRM写事务状态；部署后核对迁移状态、Django配置检查、CRM unit active、登录页HTTP 200、静态资源HTTP 200、私有媒体仍为404及未批准Host仍拒绝。不要输出测试密码/secret或业务数据。业务/权限/并发及桌面/手机流程由QA/UI按固定SHA验证，部署冒烟不能替代验收。
7. **失败边界**：迁移前任何校验、备份、哈希失败即取消更新，恢复旧应用制品即可，DB不动。迁移失败后保持服务停写，检查迁移记录及数据库状态并上报；不自动重复、反向迁移或恢复源库。迁移成功但新应用异常时，仅回旧代码可能与新schema不兼容，保持停写，等负责人决定前向修复或使用该环境刚做的备份恢复。整库/源库恢复可能删除备份后的测试写入，必须由负责人明确批准恢复库、媒体范围及备份后数据处置；预检恢复不等于对现有数据恢复的授权。

**仍缺的执行输入/证据**：最终完整SHA及各环境制品校验；QA/UI和负责人最终验收及明确执行消息；本机与云端各自停写窗口及在途写确认；各自最新数据状态与需保留媒体范围/体积；按原owner/ACL保留方式制作的最新备份及备份后数据恢复决定；最终SHA是否含0014/其他迁移的审阅结果。现有合成备份仅证明一次逻辑schema/有限计数恢复对照，未证明owner/ACL、完整应用恢复、正式生产或异地备份能力。正式生产、NAS、真实期初和切换均不在此次授权内。
# 当前任务：RELEASE-IP-UPDATE固定版本更新（2026-10-10）

**固定版本与范围**：部署应用SHA `0f835cb2086852ae20f2f82aed8a40070281a0b4`（打包提交`160b5930ca774e675916b6ac4b2ae061ac88c23d`），云端目标数据库仅`aluola_crm_dev`、本机仅`.local/demo-runtime`的`aluola_crm_local_demo`。无依赖锁变化，未同步依赖；无生产、NAS、真实期初数据或主机重启。保留Hermes与tracker/Docker原状态。

**私有恢复点**：部署前数据库分别以`pg_dump -Fc --create`完整备份（默认保留owner/ACL），本机另备份4个媒体文件；文件位于忽略目录`.local/release-ip-update-20261010`，目录700、文件600。云端dump SHA256 `ffb7c8922d2df0b319fe2e5b6306499c452520098154ec2a2fa9ae282efcd0bd`，远端`pg_restore --list`成功（471项）；本机dump SHA256 `fb46e0b972d7bc1f51c44390d21ad8181fa5aedfce64da57fd4236f241557d61`，本地列表验证成功。媒体归档SHA256 `2d3943c8ee149f075751d777588943e5a08cfbb3f810e6dc51cd06e32004c088`，gzip校验成功。仅本机demo产品`id=1`的非标准语言值“简体中文”按负责人确认更正为受控值“简中”，未动库存/成本/订单；其他语言值不作映射。迁移0014会删除旧生日字段，反向迁移有损；备份是恢复边界。

**云端**：固定artifact SHA256 `81c91222b881a9e18423bffdc43fb9c56ae4d7ed728f6bdee9f421a26ae804af`，部署前应用快照留在私有目录。0014 preflight通过、`migrate --plan`仅列0014，迁移及`migrate --check`成功，Django system check通过，静态文件收集6个。服务`aluola-crm-dev.service` active；后端仍仅监听127.0.0.1:18765。首次nginx登录探测返回502，紧接着直连上游为200且重测nginx登录200，未更改nginx；最终登录/静态/媒体/未批准Host分别为200/200/404/400。用私有销售账号执行真实网页登录，POST返回200并能访问首页200；`/sales/orders/`为404（路径不是有效入口，未推断权限结论）。

**本机演示**：确认0014待迁移仅该迁移；完成迁移、`migrate --check`、Django system check及133个静态文件收集。服务运行在127.0.0.1:8765；登录/静态/媒体/未批准Host分别为200/200/404/400。固定销售账号本机尚未建立/复验，未声称权限验收。

**边界与未决**：没有执行回滚；如要还原数据库须负责人明确批准并恢复对应完整dump，不能依赖0014 reverse。默认本机`pg_restore`不支持此自定义归档压缩，云端远程列表验证为准。应用整体权限验收仍由QA负责。

## 当前任务：LOCAL-DEMO-RUNTIME-PIN 固定本机演示运行副本（2026-10-12）

负责人按UI-HOME-REFINE写入保护要求授权切换本人管理的本机8765服务。切换前确认8765无监听进程，未终止或影响其他进程；云端未触碰。以发布包`.local/release-ip-update-20261010/aluola-crm-app-0f835cb2086852ae20f2f82aed8a40070281a0b4.tar`为唯一输入，SHA256 `81c91222b881a9e18423bffdc43fb9c56ae4d7ed728f6bdee9f421a26ae804af`，解出81个文件并逐文件对比包内散列。固定应用目录为`.local/demo-runtime/app/aluola-crm`，不依赖工作树后续HEAD/应用写入。

沿用绝对项目Python `.venv/bin/python` 和 `.local/demo-runtime/demo_settings.py`，同一`aluola_crm_local_demo`、媒体`.local/demo-runtime/media`和静态`.local/demo-runtime/static`。因副本的`BASE_DIR`改变后默认Unix socket路径超过macOS限制，仅在忽略的demo_settings内将HOST改为`RUNTIME_ROOT/pgsock`；该短别名是到原`.local/postgres-socket`的符号链接，没有改数据库/PG配置或数据。配置输出确认DB_NAME与`SELECT current_database()`均为`aluola_crm_local_demo`，最新CRM migration为`0014_user_revision_1009`；Django check无问题。当前服务PID 56309、工作目录固定副本、仅绑定127.0.0.1:8765；登录/静态/媒体/非法Host返回200/200/404/400。启动命令使用固定副本manage.py、绝对venv Python、`PYTHONPATH=<项目根>/.local/demo-runtime`及`DJANGO_SETTINGS_MODULE=demo_settings`。未运行业务写入或迁移。

# Windows 本机部署手册（2026-11-02）

## 目标与当前状态

本手册指导将 Windows 上正在运行的 CRM 更新到已验收的商品导入版本。它只覆盖现有 8001 实例，不是首次安装步骤，也不授权现在自动开始。只有用户确认 Windows 进入维护窗口后才执行。

| 项目 | 当前状态 |
| --- | --- |
| Windows 应用目录 | `D:\ALUOLA-CRM\crm-manager-deploy-35434ac` |
| 当前应用 | `35434ac3404fbe5dd34b399ce4981b315b2b11b7` |
| 更新目标 | `057de92974a8a2ec3451ff42b514d4d56c9b3e3d` |
| 目标包含 | 商品导入 `5bc00f0` 与商品补充资料 `95718bc`；独立 QA 和负责人复核通过 |
| 运行环境 | Python 3.13.14、PostgreSQL 17.6；锁定依赖不变 |
| 依赖 | 不变；本次不升级或重装 |
| 运行入口 | Waitress，仅 `127.0.0.1:8001` |
| 数据库 | `aluola_crm_dev` / `crm_dev`，当前迁移 `0019`，本次只迁移到 `0020_catalog_details_min_1030` |
| 数据性质 | 当前账号和业务记录都是测试数据；尚未导入真实商品资料 |
| 商品模板 | `docs/templates/商品批量导入模板-V1.xlsx`，必须随目标版本保留 |

## 不在本次范围

- 不启动旧目录 `D:\ALUOLA-CRM\crm-manager` 或其 8000 端口；旧代码与当前数据库迁移版本不匹配。
- 不更改或清理 `.local`、`deploy_settings.py`、密钥文件、启动器、媒体目录、数据库账号或现有测试记录；媒体继续使用当前私有 `MEDIA_ROOT`（`D:\ALUOLA-CRM\crm-manager\.local\private-media`）。
- 不导入或确认真实商品，不导入库存、客户或历史单据，不造测试业务数据，不重置账号。
- 不改云端、正式库、PostgreSQL 认证、防火墙、隧道或企业微信配置。
- 不重命名或重复创建任务计划。现有 `ALUOLA-CRM-Waitress` 任务保留；需要通过任务计划启动时复用它。

## 执行门槛

开始前由用户确认 Windows 本机可进入维护窗口、CRM 当前没有人在录入。执行者只在上述应用目录与数据库操作。密码由用户在本机浏览器自行输入，不向用户索要或在聊天中传递。

### 1. 核对当前代码和工作区

在普通 PowerShell 窗口执行：

```powershell
Set-Location 'D:\ALUOLA-CRM\crm-manager-deploy-35434ac'
$ExpectedBase = '35434ac3404fbe5dd34b399ce4981b315b2b11b7'
$TargetSha = '057de92974a8a2ec3451ff42b514d4d56c9b3e3d'
$CurrentSha = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $CurrentSha -ne $ExpectedBase) { throw 'Unexpected current commit; stop and report.' }
$Status = git status --porcelain
if ($LASTEXITCODE -ne 0) { throw 'Could not read working tree status; stop.' }
if ($Status) { throw 'Working tree contains local changes; preserve them and stop. Do not clean, stash, or reset.' }
```

若有未提交或未跟踪的用户代码改动，保留原样并报告负责人；不要通过 `reset`、`clean`、覆盖文件或 stash 继续。`.local` 应保持在原位，不复制到新目录。

### 2. 停止服务并制作可读的数据库备份

使用当前 `ALUOLA-CRM-Waitress` 任务或现有 Waitress 窗口停止 8001 实例，确认监听已释放；不要创建第二个任务或第二个服务进程。

在同一目录准备与现有启动器一致的私有 Django/数据库环境。按其现有方式设置 `.local` 的 `PYTHONPATH`、`DJANGO_SETTINGS_MODULE=deploy_settings` 以及数据库连接环境；如需读取密钥文件，只在本机进程环境中使用，不打印或粘贴。不要直接 dot-source 会启动 Waitress 的脚本，也不要改用默认 `config.settings`。

用 `.\.venv\Scripts\python.exe` 运行只读 Django 命令，核对数据库身份和当前迁移；数据库身份必须为 `aluola_crm_dev` / `crm_dev`，CRM最新迁移必须是 `0019`。不符合就停：

```powershell
.\.venv\Scripts\python.exe manage.py shell -c "from django.db import connection; c=connection.cursor(); c.execute('select current_database(), current_user'); print(c.fetchone())"
.\.venv\Scripts\python.exe manage.py showmigrations crm
```

备份前后均不得切到其他数据库。先从现有私有 settings 确认实际主机和端口，并为 PostgreSQL 客户端设置对应的 `PGHOST`、`PGPORT`、`PGUSER=crm_dev`；Django 的 `CRM_DB_*` 变量不会自动传给 `pg_dump`。口令使用已有本机私有凭据方式，不写到命令参数或输出。使用 PostgreSQL 17 客户端，在当前用户私有的 `%LOCALAPPDATA%` 下新建唯一备份目录；不覆盖旧备份，也不要将 dump 放进 ACL 未核准的共享目录：

```powershell
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$BackupDir = Join-Path $env:LOCALAPPDATA "AluolaCRM-pre-0020-$Stamp"
if (Test-Path $BackupDir) { throw 'Backup directory already exists; stop and choose a new unique path.' }
New-Item -ItemType Directory -Path $BackupDir | Out-Null
$DumpPath = Join-Path $BackupDir 'aluola_crm_dev.dump'
pg_dump --format=custom --file "$DumpPath" --dbname=aluola_crm_dev
if ($LASTEXITCODE -ne 0) { throw 'Database backup failed; do not update or migrate.' }
pg_restore --list "$DumpPath" > (Join-Path $BackupDir 'aluola_crm_dev.toc.txt')
if ($LASTEXITCODE -ne 0) { throw 'Backup readability check failed; do not update or migrate.' }
```

只有数据库身份、`0019` 状态和 `pg_restore --list` 都符合预期才继续。该检查确认备份目录可读，不等同于本次重新做完整恢复演练。

### 3. 快进更新到固定 SHA

保持 Waitress 停止。目标提交必须是当前提交的后代；不得 cherry-pick、reset、clean 或以 `main` 最新状态替代固定目标：

```powershell
git fetch origin $TargetSha
if ($LASTEXITCODE -ne 0) { throw 'Could not fetch the fixed target; stop.' }
$FetchedSha = (git rev-parse FETCH_HEAD).Trim()
if ($FetchedSha -ne $TargetSha) { throw 'Fetched SHA differs from the approved target; stop.' }
git merge-base --is-ancestor HEAD FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Target is not a fast-forward descendant; stop and report.' }
git merge --ff-only FETCH_HEAD
if ($LASTEXITCODE -ne 0 -or (git rev-parse HEAD).Trim() -ne $TargetSha) { throw 'Fast-forward update did not reach the fixed SHA; stop.' }
if (-not (Test-Path 'docs\templates\商品批量导入模板-V1.xlsx')) { throw 'Required product import template is missing; stop.' }
```

确认 `.local\deploy_settings.py`、`.local\django_secret_key.txt`、`.local\run_waitress.ps1` 及原私有媒体目录仍在原位；不覆盖、移动、复制或清理这些文件。数据库中的现有测试账号与记录保留。

### 4. 只迁移指定的 `0020`

继续使用第 2 步的私有 settings 和数据库环境。执行迁移计划，唯一待应用项必须为 `crm.0020_catalog_details_min_1030`；若出现其他待迁移项，立即停止。

```powershell
.\.venv\Scripts\python.exe manage.py migrate --plan
.\.venv\Scripts\python.exe manage.py migrate crm 0020_catalog_details_min_1030
if ($LASTEXITCODE -ne 0) { throw 'Migration failed; keep service stopped and report.' }
.\.venv\Scripts\python.exe manage.py check
if ($LASTEXITCODE -ne 0) { throw 'Django check failed; keep service stopped and report.' }
.\.venv\Scripts\python.exe manage.py migrate --check
if ($LASTEXITCODE -ne 0) { throw 'Pending migration remains; keep service stopped and report.' }
.\.venv\Scripts\python.exe manage.py collectstatic --noinput
if ($LASTEXITCODE -ne 0) { throw 'Static collection failed; report before starting.' }
```

本次数据库迁移为商品/SKU补充资料字段；不调整库存数量或成本。静态文件只收集到现有 `.local/static`，不要加 `--clear`。

### 5. 恢复 8001 并做最小验收

复用现有 `ALUOLA-CRM-Waitress` 任务或当前私有启动器，不更名、不重复创建任务；按原启动方式启动前确认 8001 没有旧实例：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\ALUOLA-CRM\crm-manager-deploy-35434ac\.local\run_waitress.ps1"
```

确认服务只监听 `127.0.0.1:8001`，打开 `http://127.0.0.1:8001/`。用户自行登录一次；在商品导入页面下载模板，确认下载成功且是随本次固定版本提供的商品模板。本次只检查入口和模板下载，不上传或确认真实商品数据。

## 失败处理与交付

- 备份、数据库身份、迁移计划或 SHA 不符合预期：停止后续步骤并报告负责人；数据库未变时可用原固定版启动器恢复服务。
- `0020` 已开始后出现迁移或应用异常：保持 8001 停止，保留 dump 和现场；不自动恢复数据库、不反向迁移，也不启动旧 8000，等待负责人处理。
- 不把登录凭据、密钥、环境变量、数据库行内容或商品明细发到聊天。
- 完成后只报告：目标 SHA、数据库名/迁移版本、dump 的 `pg_restore --list` 结果、Django 检查、静态文件结果、监听地址、登录和模板下载是否通过。

本次更新不包括云端部署、真实商品导入、库存导入、企业微信接入或隧道配置。旧版完整部署记录仍见 [Windows 本机部署手册 1028](WINDOWS-NATIVE-DEPLOY-GUIDE-1028.md)。

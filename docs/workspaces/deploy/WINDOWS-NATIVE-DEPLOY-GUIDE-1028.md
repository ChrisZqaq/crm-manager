# Windows 本机部署执行手册

**交接对象：** Windows 11 本机执行 agent

**项目目录：** `D:\ALUOLA-CRM\crm-manager`

**更新日期：** 2026-10-28

**范围：** 本机 CRM 稳定运行、静态文件和开机登录后自启；外网隧道仅记录前置条件，不在本手册授权执行。

## 给 Windows agent 的执行指令

按阶段顺序执行，先只读核验，再准备 `.local` 私有配置，获批依赖到位后才运行 Waitress/WhiteNoise。每一阶段完成后报告结果再继续；遇到停止条件即停并联系负责人。不要提交 Git、不要写数据库、不要输出任何密钥或账号凭据。

## 1. 目标与边界

在不动现有本机服务的前提下，准备经负责人固定 SHA 的新代码目录，用 Waitress 运行新版本、WhiteNoise 服务 Django 静态文件；新进程先只监听 `127.0.0.1:8001`。旧目录、旧服务和独立开发数据库先保持原状；新代码含 `0019`，数据库迁移需负责人另开窗口。

- Windows 原生运行，不安装 Linux、WSL、Docker、IIS、FRP 或额外 Windows 服务。
- 复用现有 Python/PostgreSQL 安装和当前 dev 数据库；不创建新库、不迁移数据、不运行无 `--check` 的迁移命令。
- 不打开 Windows 防火墙端口，不绑定 `0.0.0.0` 或局域网地址。
- 不读取或发送密码、`DJANGO_SECRET_KEY`、数据库环境变量值、用户凭据或密钥。Windows agent 只报告是否配置、命令退出码和非敏感路径/版本。
- 不修改 `scripts/dev.ps1`，不删除或覆盖 `.local` 中的已有内容。
- 企业微信当前暂停；不研究、不联调、不实现。

## 2. 已核实状态与停止条件

| 项目 | 已知状态 |
|---|---|
| Windows | Windows 11 / N100；用户报告 Python 3.13.14、PostgreSQL 17.6 |
| CRM | 独立 dev 库 `aluola_crm_dev`、角色 `crm_dev`、迁移 0018；用户报告 Django 检查和页面访问通过 |
| Git | HEAD `de4d9eb6ac16bc8b3f9b952c552da7d55d5496ee` 是无父提交的根快照；`caed633cfb` 不在本机 Git 对象库；仓库非 shallow |
| 用户文件 | `scripts/dev.ps1` 是未跟踪文件，必须保留；旧目录、`.venv`、`.local` 和该脚本均保持原样。不得在原目录 `git clean`、`reset`、`pull`、cherry-pick 或覆盖 |
| 本地私有目录 | 旧目录 `.local/` 已确认被 `.gitignore` 忽略；其配置/静态目录是否已存在仍需 Windows agent 检查。新代码目录只重建所需私有配置，不整目录复制 `.local` |
| Waitress/WhiteNoise | 后端已固定依赖提交 `bac28a6dd26cf04e53ab842f8c373658ae6ec0ed`，`windows` extra 锁定 Waitress 3.0.2 / WhiteNoise 6.12.0；只在隔离 macOS CPython 3.13.15 验证过锁定安装与导入，Windows 实机未验。负责人尚未提供最终发布 SHA |
| 云端 SSH | 当前有效 `AllowTcpForwarding=no`、`GatewayPorts=no`；22 端口可连不代表支持反向隧道 |

**立即停止并报告负责人**，如果数据库身份不是 `aluola_crm_dev` / `crm_dev`、旧目录 `.local` 有现成同名配置、服务已占用相关端口但无法确认归属、或需要迁移用户数据库/改云配置才能继续。新发布版本的 `0019` 在当前 0018 数据库上属于待应用迁移；`migrate --check` 报出它是预期结果，绝不能自行 `migrate`。

## 3. 阶段 A：本机只读预检

在已有、能够运行 `python manage.py check` 的 PowerShell 环境中执行。已完成的 Git 检查不用重复；不得更新代码。

```powershell
Set-Location 'D:\ALUOLA-CRM\crm-manager'
python -c "import sys; print(sys.executable)"
Test-Path .local\deploy_settings.py
Test-Path .local\django_secret_key.txt
Test-Path .local\static
```

记录 Python 可执行文件路径。若任一 `.local` 路径存在，保留并先检查用途，不要覆盖。已确认的忽略规则可以复核：

```powershell
git check-ignore -v --no-index .local/deploy_settings.py
```

在当前 dev 启动环境中只读核验数据库身份和迁移状态：

```powershell
python manage.py shell -c "from django.db import connection; connection.ensure_connection(); c=connection.cursor(); c.execute('select current_database(), current_user'); print(c.fetchone())"
python manage.py migrate --check
```

预期数据库身份为 `('aluola_crm_dev', 'crm_dev')`；迁移为 0018 且 `migrate --check` 成功。不要运行 `python manage.py migrate`、`makemigrations`、数据导入或其他写库命令。

Windows agent 应在本机阅读 `scripts/dev.ps1`，确认它如何选择 Python 环境和设置 CRM 数据库连接变量；不要把脚本全文或变量值发到聊天。如果脚本含明文秘密，不要复制到命令行、任务参数或 Git。后续启动器必须以相同的安全方式加载现有 dev 环境，否则暂停，不要猜数据库地址或口令。

## 4. 阶段 B：依赖门槛

后端已固定依赖提交 `bac28a6dd26cf04e53ab842f8c373658ae6ec0ed`，`windows` extra 锁定 Waitress 3.0.2 与 WhiteNoise 6.12.0。项目[文档入口](../../README.md)规定命令为 `uv sync --locked --extra windows`。该锁定安装与导入只在隔离 macOS CPython 3.13.15 环境验证，不能当作 Windows 实机验收；此提交也不是负责人最终发布 SHA。

`bac28a6` 只是依赖提交，不是可直接部署的最终固定 SHA。负责人验收并推送最终固定 SHA、给出规范仓库 URL 后，才在旧目录旁新建空目录，例如 `D:\ALUOLA-CRM\crm-manager-deploy-FINALSHA`。确认目标不存在后执行以下步骤；把两个占位值替换为负责人提供的值：

```powershell
$RepoUrl = 'OWNER_PROVIDED_CANONICAL_REPO_URL'
$TargetSha = 'OWNER_PROVIDED_FULL_SHA'
$TargetDir = 'D:\ALUOLA-CRM\crm-manager-deploy-FINALSHA'
if (Test-Path $TargetDir) { throw 'Target exists; choose a new empty sibling directory.' }
git clone --no-checkout $RepoUrl $TargetDir
if ($LASTEXITCODE -ne 0) { throw 'Clone failed.' }
git -C $TargetDir checkout --detach $TargetSha
if ($LASTEXITCODE -ne 0) { throw 'Checkout failed.' }
$ActualSha = (git -C $TargetDir rev-parse HEAD).Trim()
if ($ActualSha -ne $TargetSha) { throw 'Checked out SHA differs from approved SHA.' }
git -C $TargetDir status --short --branch
```

确认新目录工作树干净后，只在新目录执行 `uv sync --locked --extra windows`。原目录、旧 `.venv`、旧 `.local`、数据库和未跟踪 `scripts/dev.ps1` 完整保留；不从旧目录复制 `.local`、`.venv` 或整个启动脚本。按旧脚本确认的安全方式，只重建新代码运行必需的私有设置；不要复制 PostgreSQL 数据目录或媒体。

最终 SHA/仓库地址未给之前，只做旧目录只读检查和私有配置准备，不执行 clone、配置搬运或依赖安装；不在原目录运行 `uv sync`、`pip install`、`pull`、`reset` 或 `cherry-pick`。共享新版本含迁移 `0019`，对用户现有 0018 数据库的升级必须先提交备份/回退方案，由负责人另开窗口批准。

- 使用获批的锁定版本及安装命令；优先使用隔离的部署虚拟环境，不改动当前开发环境。
- 不自行运行 `pip install waitress whitenoise`，不升级 Django/Python/PostgreSQL，不编辑锁文件。
- 研究候选仅供比对：Waitress 3.0.2 声明支持 CPython 3.9+；WhiteNoise 6.12.0 的上游记录覆盖 Python 3.13 和 Django 5.2。最终版本必须以项目获批依赖为准：[Waitress](https://pypi.org/project/waitress/3.0.2/)、[WhiteNoise Django 配置](https://whitenoise.readthedocs.io/en/stable/django.html)、[WhiteNoise 变更记录](https://whitenoise.readthedocs.io/en/stable/changelog.html)。

## 5. 阶段 C：私有配置

只在负责人给出最终 SHA 并成功克隆的新目录准备私有配置；不要在旧目录修改或覆盖 `.local`。在新目录确认 `.local/deploy_settings.py`、`.local/django_secret_key.txt` 均不存在后再创建。不要整目录复制旧 `.local`（里面可能含数据库、媒体和旧配置）；若旧目录有媒体文件，先保留原处并报告，不要擅自映射到新代码。不要输出生成的密钥；确认新 `.local` 访问权限只覆盖需要使用该机器的账户。

```powershell
if (Test-Path .local\deploy_settings.py) { throw 'Private settings already exists; preserve and inspect locally.' }
if (Test-Path .local\django_secret_key.txt) { throw 'Secret file already exists; preserve and inspect locally.' }
if (-not (Test-Path .local)) { New-Item -ItemType Directory -Path .local | Out-Null }
```

在新目录的隔离 Python 环境里生成密钥并直接写入新 `.local`，不把密钥显示在终端或发给他人：

```powershell
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())" |
    Set-Content -NoNewline -Encoding ascii .local\django_secret_key.txt
```

创建 `.local\deploy_settings.py`，仅包含本机部署覆盖：

```python
from config.settings import *

STATIC_ROOT = BASE_DIR / ".local" / "static"
MIDDLEWARE = list(MIDDLEWARE)
MIDDLEWARE.insert(
    MIDDLEWARE.index("django.middleware.security.SecurityMiddleware") + 1,
    "whitenoise.middleware.WhiteNoiseMiddleware",
)
```

这个 overlay 通过 `DJANGO_SETTINGS_MODULE=deploy_settings` 加载；启动前将 `.local` 加入 `PYTHONPATH`。启动器还必须先从私有密钥文件设置 `DJANGO_SECRET_KEY`，再启动 Python，因为基础设置在 `DEBUG=0` 时会要求该变量。保持 `DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1`。**不要覆盖** `CRM_DB_NAME/USER/PASSWORD/HOST/PORT`；复用阶段 A 已核实的 dev 连接环境。

基础设置的 `STATIC_URL` 是相对路径 `static/`。本轮先保持不变，通过后面的浏览器网络请求检查实际静态 URL；若深层页面 CSS/JS 404，停止并报告负责人，不改共享 settings 或模板。

## 6. 阶段 D：配置检查与静态文件

在新目录的 PowerShell 会话及后续启动脚本中设置：

```powershell
$env:PYTHONPATH = (Resolve-Path .local).Path + [IO.Path]::PathSeparator + $env:PYTHONPATH
$env:DJANGO_SETTINGS_MODULE = 'deploy_settings'
$env:DJANGO_DEBUG = '0'
$env:DJANGO_SECRET_KEY = (Get-Content .local\django_secret_key.txt -Raw).Trim()
$env:DJANGO_ALLOWED_HOSTS = 'localhost,127.0.0.1'
```

同样按阶段 A 读过的 `scripts/dev.ps1` 安全复用现有 dev 数据库环境；不要复制原 `.local` 或输出/硬编码密码。新版本连到 `aluola_crm_dev` / `crm_dev` 后，运行只读检查：

```powershell
python manage.py check
python manage.py migrate --check
python manage.py shell -c "from django.db import connection; c=connection.cursor(); c.execute('select current_database(), current_user'); print(c.fetchone())"
```

`migrate --check` 若报告 0019 尚未应用，这是当前预期状态；保存退出状态并停止在此，不运行迁移。确认数据库身份仍是 `aluola_crm_dev` / `crm_dev` 后再收集静态文件：

```powershell
python manage.py collectstatic --noinput
```

目标只能是 `.local/static`。首次执行前确认该目录不存在或为空；不要加 `--clear`。WhiteNoise 只服务静态文件，`MEDIA_ROOT` 仍留在 `.local/private-media`，不得通过 WhiteNoise 或 Web 服务器公开用户上传媒体。

## 7. 阶段 E：手动运行与验收

仅在负责人给出已验收 SHA 和隔离交付目录后，按项目锁定方式执行 `uv sync --locked --extra windows` 并确认使用的是该目录虚拟环境。不得在原快照上安装。新版本仅先用独立端口 8001 手动启动，旧的 8000 服务保持原样；不要停止或替换旧服务。

建立新目录内的本机启动脚本 `.local\run_waitress.ps1`。将 `$ProjectRoot` 和 `$Python` 替换为新目录及其隔离虚拟环境的绝对路径。先阅读旧 `scripts/dev.ps1`，只把它安全设置 dev 数据库环境的方式放进下方标注位置；不要 dot-source 一个会自动启动 runserver 的脚本，也不要打印变量值。若不能安全复用当前 DB 环境，暂停。

```powershell
$ErrorActionPreference = 'Stop'
$ProjectRoot = 'D:\ALUOLA-CRM\crm-manager-deploy-FINALSHA'
$Python = 'REPLACE_WITH_RECORDED_PYTHON_EXE'
Set-Location $ProjectRoot

# 在这里加载现有 dev 数据库连接环境；不要输出环境变量值。
$env:PYTHONPATH = (Resolve-Path .local).Path + [IO.Path]::PathSeparator + $env:PYTHONPATH
$env:DJANGO_SETTINGS_MODULE = 'deploy_settings'
$env:DJANGO_DEBUG = '0'
$env:DJANGO_SECRET_KEY = (Get-Content .local\django_secret_key.txt -Raw).Trim()
$env:DJANGO_ALLOWED_HOSTS = 'localhost,127.0.0.1'

& $Python -c "from waitress import serve; from config.wsgi import application; serve(application, host='127.0.0.1', port=8001)"
```

该命令是前台长驻进程，验收时保持窗口运行。成功启动后，在另一个 PowerShell 检查监听地址与 HTTP：

```powershell
Get-NetTCPConnection -State Listen -LocalPort 8001 |
    Select-Object LocalAddress, LocalPort, OwningProcess
curl.exe -sS -o NUL -w "%{http_code}`n" http://127.0.0.1:8001/accounts/login/
curl.exe -sS -o NUL -w "%{http_code}`n" -H "Host: invalid.example" http://127.0.0.1:8001/
```

验收要求：只监听 `127.0.0.1:8001`；匿名登录页成功；非法 Host 返回 400；静态资源返回 200。由于新共享代码含 0019、用户 DB 仍是 0018，当前只验收匿名登录页和静态资源，不登录、不打开依赖新字段的业务页面、不写业务数据。完整登录/深层页面验收及自启必须等负责人批准数据库备份/迁移窗口并完成迁移后再做。不要在报告中写账号或密码。不要测试真实客户附件；确认 WhiteNoise 配置没有将 `MEDIA_ROOT` 对外提供。

如果静态资源 404、应用连到其他数据库、非法 Host 未被拒绝、或监听落在 `0.0.0.0`/局域网地址，立即停止新服务并报告，不通过改共享代码/数据库/防火墙来“修好”。

## 8. 数据库迁移门槛：0018 → 0019

当前用户开发库仍为 0018，而负责人最终固定的新代码包含 0019。新代码的 `migrate --check` 报 0019 未应用是预期结果；它只做检查，不是迁移授权。迁移前必须先向负责人提交并获批：

1. 新旧代码的完整 SHA、目标数据库名/角色、当前迁移号和 `python manage.py migrate --plan` 结果。
2. 数据库与媒体配对备份的实际路径、大小/校验结果，以及备份可读性验证（例如 `pg_restore --list`）；备份放在不会被新 clone 覆盖的独立本机目录。
3. 明确回退步骤：停止新版本 8001、旧版本保持可用；如迁移失败，由负责人确认恢复数据库/媒体备份的目标与命令。不得自行尝试反向迁移或覆盖数据库。
4. 负责人明确批准对 `aluola_crm_dev` 执行迁移的时间窗口。

只有窗口开启后，才可在新 SHA 对应虚拟环境中先复核 `migrate --plan`，再按批准范围执行 `python manage.py migrate`，随后 `python manage.py migrate --check` 并做只读 schema/数据核验。任何失败即停并按已批准回退方案处理；不触正式云库或其他数据库。

## 9. 阶段 F：登录后自启

**当前不创建任务计划。**只有 0019 迁移获批并成功、登录及深层业务页全部验收通过后，再创建 Windows 任务计划。使用非管理员的当前 CRM 本机用户，选择“用户登录时”触发和“仅当用户登录时运行”，不勾选最高权限；这样不会把用户密码另存为任务凭据。任务执行新目录 `.local\run_waitress.ps1`，起始目录为新项目根目录。设置失败后每分钟重试、最多 3 次；若任务已运行，选择不启动第二实例。

任务启动器必须自行加载阶段 A 验证的数据库环境及新 `.local` 密钥文件，因为计划任务不会继承手动 PowerShell 会话变量。若无法在不暴露凭据的情况下复用当前本机环境，先停在这里。启动后复验监听、登录页和深层页；重启 Windows 后由用户登录再复验。不要安装 NSSM 或其他服务管理器。

## 10. 回退与数据保护

- 手动进程：回到新版本 8001 的 Waitress 窗口按 `Ctrl+C`；不要停旧 8000。
- 计划任务：先在任务计划程序中“结束”并“禁用”本任务；确认 8001 释放，旧目录/旧服务保持原样。
- 配置：备份后把新目录创建的 `.local\deploy_settings.py`、`.local\run_waitress.ps1` 改名留存；不要删除旧 `.local`，不要覆盖 `.local\private-media` 或 PostgreSQL 数据目录。
- 未获迁移窗口时不运行数据库迁移或数据清理。若启动失败，保留错误码/非敏感堆栈摘要，回报负责人；不要更换数据库、重建库或重置账号。
- 不新增 Waitress 日志器或无限增长日志文件；沿用应用现有获批日志方式，首轮问题通过前台窗口和任务计划“上次运行结果”诊断。

## 11. 外网访问/隧道：当前不执行

云端目前有效 SSH 配置 `AllowTcpForwarding=no`，不能从 Windows 直接建立 SSH `-R`。当前没有专用隧道账号/密钥、目标测试主机名、独立 HTTPS 证书或远端写入窗口。不要生成/发送密钥，不改 `sshd`、Nginx、DNS、安全组、Windows 防火墙或端口，不安装 FRP，不连接正式 CRM 域名或 `18888`。

只有负责人另行批准专用账号、用户级 SSH 限定、单一 loopback 远端端口及独立 HTTPS 测试入口后，才另开隧道步骤。隧道策略记录见同目录 `WORK.md` 的 `WINDOWS-NATIVE-DEPLOY-1028` 小节。

## 12. 完成交付格式

Windows agent 完成后只报告：Python 可执行路径（不含用户目录外的隐私部分可脱敏）、依赖批准来源/版本、`check` 与 `migrate --check` 退出状态、已核对的数据库名/角色、静态资源验收结果、监听地址、任务计划触发方式及回退是否验证。不得报告 Secret、数据库密码、登录凭据、密钥、完整 `.env` 或真实客户数据。

**当前预迁移检查标准：**新目录固定 SHA 正确、锁定依赖在 Windows 安装/导入通过、Django check 通过、0018 数据库身份确认、0019 待迁移状态如实记录、Waitress 仅 loopback:8001、匿名登录页与静态资源通过；不登录、不写业务数据、不创建自启任务。

**完整本机部署标准：**负责人批准并完成 0019 数据库迁移及备份/回退验证后，登录与深层页面资源通过、媒体仍为私有、任务计划按用户登录启动且可禁用回退。外网隧道不属于本手册完成标准。

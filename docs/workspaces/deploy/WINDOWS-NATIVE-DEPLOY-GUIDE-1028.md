# Windows 本机部署执行手册

**交接对象：** Windows 11 本机执行 agent

**项目目录：** `D:\ALUOLA-CRM\crm-manager`

**更新日期：** 2026-11-02

**范围：** 保留 Windows 本机部署及 `0019` 历史记录；新增第 13 节，供商品导入任务通过独立 QA 后一次性更新到负责人指定版本并迁移开发库 `0019 → 0020`。本草案不代表现在开始执行。

## 给 Windows agent 的执行指令

历史阶段 1–7 已有执行记录。当前后续只按第 13 节继续：等待商品导入独立 QA 通过、负责人提供完整目标 SHA、用户确认 Windows 窗口后再手动执行。遇到停止条件即停并报告；不要提交 Git、不要改旧目录、不要输出密钥/账号凭据。

## 1. 目标与边界

Windows 当前以固定版 `35434ac` 运行 Waitress/WhiteNoise，监听 `127.0.0.1:8001`，开发库 `aluola_crm_dev` / `crm_dev` 已在 `0019`。下一次只在第 13 节门槛满足后升级到负责人固定版本及 `0020`；云端正式库和真实库存/历史业务导入不在范围。

- Windows 原生运行，不安装 Linux、WSL、Docker、IIS、FRP 或额外 Windows 服务。
- 复用现有 Python/PostgreSQL 安装和当前 dev 数据库；不创建新库、不迁移业务数据、不运行第 13 节获批的 `0020` 以外迁移。
- 不打开 Windows 防火墙端口，不绑定 `0.0.0.0` 或局域网地址。
- 不读取或发送密码、`DJANGO_SECRET_KEY`、数据库环境变量值、用户凭据或密钥。Windows agent 只报告是否配置、命令退出码和非敏感路径/版本。
- 不修改 `scripts/dev.ps1`，不删除或覆盖 `.local` 中的已有内容。
- 企业微信当前暂停；不研究、不联调、不实现。

## 2. 已核实状态与停止条件

| 项目 | 已知状态 |
|---|---|
| Windows | Windows 11 / N100；用户报告 Python 3.13.14、PostgreSQL 17.6 |
| CRM | Windows 当前运行版 `35434ac`，开发库 `aluola_crm_dev` / `crm_dev` 已迁移至 `0019`；用户已确认本机登录。此状态来自用户/部署记录，非本机独立复验 |
| Git | HEAD `de4d9eb6ac16bc8b3f9b952c552da7d55d5496ee` 是无父提交的根快照；`caed633cfb` 不在本机 Git 对象库；仓库非 shallow |
| 用户文件 | `scripts/dev.ps1` 是未跟踪文件，必须保留；旧目录、`.venv`、`.local` 和该脚本均保持原样。不得在原目录 `git clean`、`reset`、`pull`、cherry-pick 或覆盖 |
| 本地私有目录 | 旧目录 `.local/` 已确认被 `.gitignore` 忽略；内部内容未核实。新代码目录有独立 `.local`，不复制旧目录的设置、数据库或媒体 |
| 固定交付代码 | 负责人核验 Windows 本轮固定目标 SHA 为 `35434ac3404fbe5dd34b399ce4981b315b2b11b7`（应用 `31df9e9` + Windows extra `bac28a6` + 交付文档）。后续 `main` 可继续包含文档提交；不要要求 `main` 当前 SHA 等于该目标，只核对新 clone checkout 的完整 SHA |
| Waitress/WhiteNoise | `windows` extra 锁定 Waitress 3.0.2 / WhiteNoise 6.12.0；只在隔离 macOS CPython 3.13.15 验证过锁定安装与导入，Windows 实机未验 |
| 云端 SSH | 当前有效 `AllowTcpForwarding=no`、`GatewayPorts=no`；22 端口可连不代表支持反向隧道 |

**立即停止并报告负责人**，如果目标 SHA 无法取得或核对、数据库身份不是 `aluola_crm_dev` / `crm_dev`、当前迁移不是 `0019`、迁移计划不止获批的 `0020`、备份无法验证，或 8001 进程归属不明。不得连接或迁移正式库 `alola_crm`。

## 3. 阶段 1：新目录获取固定版本

在 PowerShell 中使用旧目录配置的 `origin`，在旁边创建全新目录并 checkout 固定 SHA。不要在旧目录 pull/reset/cherry-pick，也不要覆盖它。

```powershell
$OldRoot = 'D:\ALUOLA-CRM\crm-manager'
$RepoUrl = git -C $OldRoot config --get remote.origin.url
$RemoteExit = $LASTEXITCODE
if ($RemoteExit -ne 0 -or -not $RepoUrl) { throw 'No origin URL; stop and ask for an approved HTTPS clone URL.' }
$RepoUrl = $RepoUrl.Trim()
$TargetSha = '35434ac3404fbe5dd34b399ce4981b315b2b11b7'
$TargetDir = 'D:\ALUOLA-CRM\crm-manager-deploy-35434ac'
if ($RepoUrl -match '://[^/]*@') { throw 'Origin embeds credentials; do not clone or print it. Use Git Credential Manager with a credential-free URL.' }
if (Test-Path $TargetDir) { throw 'Target exists; choose a new empty sibling directory.' }
git clone --no-checkout $RepoUrl $TargetDir
if ($LASTEXITCODE -ne 0) { throw 'Clone failed.' }
git -C $TargetDir checkout --detach $TargetSha
if ($LASTEXITCODE -ne 0) { throw 'Checkout failed.' }
$ActualSha = (git -C $TargetDir rev-parse HEAD).Trim()
if ($ActualSha -ne $TargetSha) { throw 'Checked out SHA differs from approved SHA.' }
$Status = git -C $TargetDir status --porcelain
if ($LASTEXITCODE -ne 0 -or $Status) { throw 'New checkout is not clean; stop and inspect.' }
"clone_verified_sha=$ActualSha"
"working_tree_clean=True"
```

确认输出 SHA 完全匹配且工作树干净后停止并报告结果。若 `origin` 不存在/含嵌入凭据，或 clone/checkout 失败，不要打印或粘贴 URL、凭据或私钥；请负责人提供无凭据 HTTPS 地址并使用本机 Git Credential Manager。若该固定 SHA 无法从远端取得，停止并回报，不使用 `main` 最新版或来源不明 ZIP。

旧目录、旧 `.venv`、旧 `.local`、数据库和未跟踪 `scripts/dev.ps1` 保持原样。新目录之外不安装依赖、不做版本切换。

## 4. 阶段 2：安装锁定依赖

只在新目录执行项目批准的锁文件安装。`windows` extra 锁定 Waitress 3.0.2 与 WhiteNoise 6.12.0；安装过程此前只在隔离 macOS CPython 3.13.15 验证，Windows 实机结果须单独报告。

```powershell
Set-Location 'D:\ALUOLA-CRM\crm-manager-deploy-35434ac'
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw 'uv is not available; stop and ask before installing tooling.' }
uv sync --locked --extra windows
if ($LASTEXITCODE -ne 0) { throw 'Locked dependency installation failed.' }
.\.venv\Scripts\python.exe -c "import sys, waitress, whitenoise; from importlib.metadata import version; print('python=' + sys.version.split()[0]); print('waitress=' + version('waitress')); print('whitenoise=' + version('whitenoise'))"
```

确认 Python 和两个包可导入后报告版本、退出状态，再进入下一阶段。不要运行 `pip install`、不升级锁文件中的包、不修改依赖。

如果提示锁文件需要更新，先不要运行 `uv lock` 或编辑 `uv.lock`。检查本次 PowerShell 会话是否指定了镜像源；移除镜像覆盖、使用锁文件原有源后再执行同一锁定安装。不得因镜像解析差异重写 lock。Windows 实机已遇到此问题：去掉清华镜像后 `uv lock --check` 通过，默认 PyPI 安装与锁版本一致。

## 5. 阶段 3：私有配置

只在已核对固定 SHA `35434ac3404fbe5dd34b399ce4981b315b2b11b7` 的新目录准备私有配置；不要在旧目录修改或覆盖 `.local`。在新目录确认 `.local/deploy_settings.py`、`.local/django_secret_key.txt` 均不存在后再创建。不要整目录复制旧 `.local`（里面可能含数据库、媒体和旧配置）；经负责人批准，新版只在私有 settings 中将 `MEDIA_ROOT` 指向旧版私有媒体目录，不复制、移动或公开媒体。不要输出生成的密钥；确认新 `.local` 访问权限只覆盖需要使用该机器的账户。

```powershell
if (Test-Path .local) { throw 'Private directory already exists; preserve and inspect locally before continuing.' }
New-Item -ItemType Directory -Path .local | Out-Null
$CurrentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
icacls .local /inheritance:r
if ($LASTEXITCODE -ne 0) { throw 'Could not restrict .local permissions.' }
icacls .local /grant:r "$($CurrentIdentity):(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F'
if ($LASTEXITCODE -ne 0) { throw 'Could not set .local permissions.' }
icacls .local
```

确认 ACL 只包含当前运行用户、SYSTEM 与本机 Administrators；若输出仍有其他用户/组，暂停并先纠正权限，不写入密钥。

在新目录的隔离 Python 环境里生成密钥并由 Python 直接写入新 `.local`，不显示密钥，也不经过 PowerShell 管道：

```powershell
if (Test-Path .local\django_secret_key.txt) { throw 'Secret file already exists; preserve it and stop.' }
.\.venv\Scripts\python.exe -c "from pathlib import Path; from django.core.management.utils import get_random_secret_key; Path('.local/django_secret_key.txt').write_text(get_random_secret_key(), encoding='ascii')"
if ($LASTEXITCODE -ne 0) { throw 'Could not create the secret file.' }
```

创建 `.local\deploy_settings.py`，仅包含本机部署覆盖：

```python
from config.settings import *

STATIC_ROOT = BASE_DIR / ".local" / "static"
MEDIA_ROOT = r"D:\ALUOLA-CRM\crm-manager\.local\private-media"
MIDDLEWARE = list(MIDDLEWARE)
MIDDLEWARE.insert(
    MIDDLEWARE.index("django.middleware.security.SecurityMiddleware") + 1,
    "whitenoise.middleware.WhiteNoiseMiddleware",
)
```

将上述代码保存为新目录 `.local\deploy_settings.py`。两版本连接同一数据库，其附件记录引用现有媒体相对路径，因此新版本复用旧目录私有媒体存储；不要复制媒体或使用空的新媒体根。先确认这个路径确为旧版当前 `MEDIA_ROOT`，且新进程用户可读写；不要为此放宽为 Everyone。这个 overlay 通过 `DJANGO_SETTINGS_MODULE=deploy_settings` 加载；启动前将 `.local` 加入 `PYTHONPATH`。启动器还必须先从私有密钥文件设置 `DJANGO_SECRET_KEY`，再启动 Python，因为基础设置在 `DEBUG=0` 时会要求该变量。保持 `DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1`。**不要覆盖** `CRM_DB_NAME/USER/PASSWORD/HOST/PORT`；复用旧 dev 启动脚本中安全设置这些环境的方式。

基础设置的 `STATIC_URL` 是相对路径 `static/`。本轮先保持不变，通过后面的浏览器网络请求检查实际静态 URL；若深层页面 CSS/JS 404，停止并报告负责人，不改共享 settings 或模板。

## 6. 阶段 4：配置检查与静态文件

在新目录的 PowerShell 会话及后续启动脚本中设置：

```powershell
$env:PYTHONPATH = (Resolve-Path .local).Path + [IO.Path]::PathSeparator + $env:PYTHONPATH
$env:DJANGO_SETTINGS_MODULE = 'deploy_settings'
$env:DJANGO_DEBUG = '0'
$env:DJANGO_SECRET_KEY = (Get-Content .local\django_secret_key.txt -Raw).Trim()
$env:DJANGO_ALLOWED_HOSTS = 'localhost,127.0.0.1'
```

后续 Django 命令均使用新目录的 `.\.venv\Scripts\python.exe`，避免误用旧目录解释器。

在连接数据库前，先在本机阅读旧目录 `scripts/dev.ps1`，确认它安全设置 dev 数据库环境的方式；不要 dot-source 可能会启动 runserver 的脚本，也不要复制旧 `.local` 或输出/硬编码密码。将安全设置环境的必要步骤加入新启动器。随后连接数据库并核对身份为 `aluola_crm_dev` / `crm_dev`，再运行只读检查：

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py migrate --check
.\.venv\Scripts\python.exe manage.py shell -c "from django.db import connection; c=connection.cursor(); c.execute('select current_database(), current_user'); print(c.fetchone())"
```

`migrate --check` 若报告 0019 尚未应用，这是当前预期状态；记录退出状态，不运行迁移，也不要登录或做业务操作。确认数据库身份仍是 `aluola_crm_dev` / `crm_dev` 后继续收集静态文件：

```powershell
.\.venv\Scripts\python.exe manage.py collectstatic --noinput
```

目标只能是 `.local/static`。首次执行前确认该目录不存在或为空；不要加 `--clear`。WhiteNoise 只服务静态文件；共享的 `MEDIA_ROOT` 不得通过 WhiteNoise 或 Web 服务器公开用户上传媒体。复验 `/media/` 与路径穿越均不可访问。

## 7. 阶段 5：手动运行与验收

确认新目录 SHA 正确且依赖安装已通过。新版本仅先用独立端口 8001 手动启动，旧的 8000 服务保持原样；不要停止或替换旧服务。

建立新目录内的本机启动脚本 `.local\run_waitress.ps1`。将 `$ProjectRoot` 和 `$Python` 设置为新目录及其隔离虚拟环境的绝对路径。将前一阶段从旧 `scripts/dev.ps1` 核实的安全数据库环境设置方式放在标注位置；不要 dot-source 会自动启动 runserver 的脚本，也不要打印变量值。若不能安全复用当前 DB 环境，暂停。

```powershell
$ErrorActionPreference = 'Stop'
$ProjectRoot = 'D:\ALUOLA-CRM\crm-manager-deploy-35434ac'
$Python = (Resolve-Path (Join-Path $ProjectRoot '.venv\Scripts\python.exe')).Path
Set-Location $ProjectRoot

# 在这里加载现有 dev 数据库连接环境；不要输出环境变量值。
$env:PYTHONPATH = (Resolve-Path .local).Path + [IO.Path]::PathSeparator + $env:PYTHONPATH
$env:DJANGO_SETTINGS_MODULE = 'deploy_settings'
$env:DJANGO_DEBUG = '0'
$env:DJANGO_SECRET_KEY = (Get-Content .local\django_secret_key.txt -Raw).Trim()
$env:DJANGO_ALLOWED_HOSTS = 'localhost,127.0.0.1'

& $Python -c "from waitress import serve; from config.wsgi import application; serve(application, host='127.0.0.1', port=8001)"
```

从另一个 PowerShell 会话使用下列命令启动脚本。`-ExecutionPolicy Bypass` 仅作用于此 PowerShell 进程，不修改机器或用户的持久执行策略：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\ALUOLA-CRM\crm-manager-deploy-35434ac\.local\run_waitress.ps1"
```

该命令启动前台长驻进程，验收时保持窗口运行。成功启动后，在另一个 PowerShell 检查监听地址与 HTTP：

```powershell
Get-NetTCPConnection -State Listen -LocalPort 8001 |
    Select-Object LocalAddress, LocalPort, OwningProcess
curl.exe -sS -o NUL -w "%{http_code}`n" http://127.0.0.1:8001/accounts/login/
curl.exe -sS -o NUL -w "%{http_code}`n" -H "Host: invalid.example" http://127.0.0.1:8001/
```

验收要求：只监听 `127.0.0.1:8001`；匿名登录页成功；非法 Host 返回 400；静态资源返回 200。由于新共享代码含 0019、用户 DB 仍是 0018，当前只验收匿名登录页和静态资源，不登录、不打开依赖新字段的业务页面、不写业务数据。完整登录/深层页面验收及自启必须等数据库迁移窗口完成后再做。不要在报告中写账号或密码。不要测试真实客户附件；确认 WhiteNoise 配置没有将 `MEDIA_ROOT` 对外提供。

如果静态资源 404、应用连到其他数据库、非法 Host 未被拒绝、或监听落在 `0.0.0.0`/局域网地址，立即停止新服务并报告，不通过改共享代码/数据库/防火墙来“修好”。

## 8. 阶段 6：开发库 0018 → 0019 单次维护窗口（历史记录）

**负责人已批准一次性窗口：2026-10-29 14:00（Asia/Shanghai）。**Windows agent须由用户确认就绪后手动开始；不设自动任务，时间到达不会自动迁移。范围仅 `aluola_crm_dev` / `crm_dev` 与固定 SHA `35434ac3404fbe5dd34b399ce4981b315b2b11b7`。正式库 `alola_crm`、云端、业务数据导入不在范围。

用户报告阶段 1–5 已完成：新版本只监听 `127.0.0.1:8001`，数据库仍为0018且0019待应用；`migrate --check`返回码1是预期结果。负责人同意本轮新版复用旧版私有媒体目录，因为数据库中的凭证图片记录引用这些文件。该目录仅通过私有 `MEDIA_ROOT` 使用，不由静态服务器提供。

### 开始前

在窗口前准备并于开始时再次确认：

- 新目录 SHA、工作树、数据库身份正确；`migrate --plan` 只列出 CRM 的 `0019`，`pg_dump --version` 为 PostgreSQL 17，目标数据库为 `aluola_crm_dev`。
- 确认监听 `8000` 与 `8001` 的进程确为本任务这两个应用。只允许在窗口中停止这两个已确认的应用进程；不要停止 PostgreSQL 或其他进程。
- 确认旧媒体目录 `D:\ALUOLA-CRM\crm-manager\.local\private-media` 存在、确属 `aluola_crm_dev` 当前附件根目录，附件记录与文件仍对应；新运行用户已有必要访问权限时不改 ACL。若目录、权限或归属不符，停止并报告。不要复制/移动附件，也不要扩大 ACL。
- 备份目标为 `D:\ALUOLA-CRM\backups\2026-10-29-pre-0019\`。目录须私有且不存在/为空；已存在内容时不要覆盖，改用新的时间戳目录并记录。备份口令只通过本机安全提示或既有凭据机制提供，不能放在命令参数、历史、聊天或 Git。

### 窗口内操作

1. 记录开始时间，再次核对数据库名/角色和迁移计划。停止已确认属于本任务的 8000/8001 应用；确认两个端口释放。不要停止数据库服务。
2. 在私有备份目录创建完整数据库自定义格式备份和旧媒体目录压缩备份。数据库备份必须非空且 `pg_dump` 退出码为 0；运行 `pg_restore --list` 并确认退出码为 0。分别记录数据库与媒体备份文件的 SHA-256。任何备份失败都不迁移。
3. **预检和备份最长 15 分钟。**若超过 15 分钟，不开始迁移；保留现场/部分备份供检查，恢复旧 8000 并报告。不能为赶时间跳过验证。开始迁移后不要因窗口计时而杀进程；8000/8001保持停写，直到迁移和只读核验完成。用户估计迁移约1分钟，但实际时长以命令完成为准。
4. 备份通过且 `migrate --plan` 仍只含 0019 后，仅执行目标迁移：

   ```powershell
   .\.venv\Scripts\python.exe manage.py migrate crm 0019
   if ($LASTEXITCODE -ne 0) { throw 'Migration failed; stop and report.' }
   .\.venv\Scripts\python.exe manage.py migrate --check
   if ($LASTEXITCODE -ne 0) { throw 'Migrations remain unapplied; stop and report.' }
   .\.venv\Scripts\python.exe manage.py check
   ```

   迁移只增加三个字段，不补历史编号、不删除数据。迁移开始后不要强制结束进程；如命令失败或验证异常，停止并报告，不反向迁移、不删列、不自行覆盖数据库。
5. 做只读核验：确认 CRM `0019` 已应用；比较迁移前后数据摘要，至少确认 4 张销售单、4 条明细、17 条业务操作、8 条库存流水、账号/付款/库存数据及 3 个凭证文件数量未意外变化，并确认旧销售单 `order_number` 仍为空。只报告一致/不一致，不发送个人资料、逐行数据或凭证内容。
6. 0019迁移已完成后恢复新目录Waitress。先打开 `http://127.0.0.1:8001/`；若页面不可用且端口没有监听，再用新目录私有启动脚本启动。只确认监听地址为 `127.0.0.1`，再用已有dev账号完成一次真实登录即可供本机用户试用；不重复跑13页/全量矩阵，不再迁移、不造单、不重置账号。旧8000保持停止。

窗口结束时记录开始/结束时间、目标 SHA、迁移/check 结果、备份路径/大小/哈希、只读摘要一致性和 8001 验收；不得记录任何秘密或个人资料。若需要回退，优先停止新 8001、恢复旧 8000，保留兼容的新增字段；不要默认反迁移。若需恢复数据库/媒体备份或处理 ACL 异常，停止并报告负责人。

## 9. 阶段 7：当前用户登录后自启

负责人已批准本机用户登录后启动。先检查现有任务 `ALUOLA-CRM-Waitress`：若存在且运行用户及启动动作确属本项目，就复用原任务；若不存在，先确认没有其它等价的本项目任务，再创建一个名为 `ALUOLA-CRM-Waitress` 的任务。发现同名任务归属不明，或存在等价任务但配置不符时，停止并报告；不因名称较短而另建重复任务。

动作使用已验证私有启动器，不把数据库口令或密钥写到任务参数、命令行或日志：程序 `powershell.exe`；参数 `-NoProfile -ExecutionPolicy Bypass -File "D:\ALUOLA-CRM\crm-manager-deploy-35434ac\.local\run_waitress.ps1"`；起始目录 `D:\ALUOLA-CRM\crm-manager-deploy-35434ac`。失败后每1分钟重试，最多3次；若任务已运行，选择不启动新实例。创建后可随时禁用作为回退。仅验证 `127.0.0.1:8001` 监听及一次已有账号登录，不要求全页矩阵或重启Windows验收。不要安装NSSM或其他服务管理器。

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

Windows agent 完成后只报告：固定 SHA、Python 版本、依赖版本/安装状态、`check` 与 `migrate --check` 退出状态、已核对的数据库名/角色、静态资源结果、监听地址；若迁移与自启窗口未开，明确报告“未迁移、未创建任务计划”。不得报告 Secret、数据库密码、登录凭据、密钥、完整 `.env` 或真实客户数据。

**阶段 1–5 预迁移交付标准：**新目录固定 SHA 正确、锁定依赖在 Windows 安装/导入通过、Django check 通过、0018 数据库身份确认、0019 待迁移状态如实记录、Waitress 仅 loopback:8001、匿名登录页与静态资源通过；不登录、不写业务数据、不创建自启任务。用户已报告该阶段完成，结果见部署工程师 WORK.md 的 Windows 执行记录。

**完整本机部署标准：**已完成当前获批版本所需迁移及备份验证，登录和目标页面通过、媒体仍为私有；自启动证据按当前计划单独核实。外网隧道不属于本手册完成标准。

## 13. 商品导入 QA 通过后一次更新草案（0019 → 0020）

**仅在**商品批量导入固定版通过独立浏览器 QA、负责人给出完整目标 SHA、用户确认本机维护窗口后执行。当前 Windows 运行版为 `35434ac` / 数据库迁移 `0019`；只更新 `crm-manager-deploy-35434ac` 和 `aluola_crm_dev`，不碰旧目录或云端正式环境。

1. 用现有目录；核对 `git status --short` 为空且当前提交为 `35434ac`。若工作树有用户改动，原样保留并停止报告，不清理、不 stash、不 reset。目标 SHA 尚未下发前不执行更新。
2. 确认数据库仍为 `aluola_crm_dev` / `crm_dev`、迁移为 `0019`，停止 Waitress 写入并做一份新的 PostgreSQL custom dump；以 `pg_restore --list` 验证可读。身份、迁移版本或备份验证不符即停止，不更新代码或迁移；数据库未变时可用现有启动器恢复旧版服务后报告。
3. 停服状态下按负责人给出的完整 SHA 执行 `fetch`；确认该提交是当前 `35434ac` 的后代后用 `git merge --ff-only` 更新。禁止 `reset --hard`、`clean`、覆盖旧根快照。确认现有 `.local`、私有 `deploy_settings.py`、密钥文件、启动器及媒体目录原位保留，并确认 `docs/templates/商品批量导入模板-V1.xlsx` 存在。
4. 在沿用第 6 节既有私有环境（`PYTHONPATH` 包含 `.local`、`DJANGO_SETTINGS_MODULE=deploy_settings`，数据库连接环境与现有启动器一致）的 PowerShell 会话中，`manage.py migrate --plan` 只可显示 `crm.0020_catalog_details_min_1030`。执行 `.\.venv\Scripts\python.exe manage.py migrate crm 0020_catalog_details_min_1030`；随后用同一环境运行 `manage.py check`、`manage.py migrate --check` 和 `manage.py collectstatic --noinput`。不得落回默认 settings。此次迁移仅增加选填产品/SKU资料字段，不改库存数量、成本或账号。
5. 用现有启动器恢复 8001：`powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\ALUOLA-CRM\crm-manager-deploy-35434ac\.local\run_waitress.ps1"`。确认只监听 `127.0.0.1:8001`，完成一次登录，并在商品导入页面下载模板核对成功；本窗口不上传或确认真实商品数据。账号保留在原数据库中，不运行 `createsuperuser`，不造数、不导库存/客户/历史单。

任一步失败即停止并报告负责人，保留备份和现场；迁移后异常时保持服务停止，不自动恢复数据库、不反向迁移、不切换云端或隧道。交付只报告目标 SHA、备份可读性、迁移/check 结果、8001 监听及模板下载结果，不包含口令、凭据或商品明细。

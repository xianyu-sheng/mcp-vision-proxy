# 一键安装脚本 - Ctrl+Alt+V 图片粘贴助手
# 使用方式：右键 -> 使用 PowerShell 运行（需要管理员权限）
# 或者：powershell -ExecutionPolicy Bypass -File setup.ps1

param(
    [switch]$Uninstall,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$MCP_PROXY_DIR = $SCRIPT_DIR

# ── 颜色定义 ──────────────────────────────────────────────────────
function Write-Step { param($msg) Write-Host "[步骤] $msg" -ForegroundColor Cyan }
function Write-Success { param($msg) Write-Host "[OK] $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "[警告] $msg" -ForegroundColor Yellow }
function Write-Err { param($msg) Write-Host "[错误] $msg" -ForegroundColor Red }
function Write-Info { param($msg) Write-Host "       $msg" -ForegroundColor Gray }

# ── Banner ────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  ╔═══════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "  ║   Ctrl+Alt+V 图片粘贴助手 - 一键安装程序           ║" -ForegroundColor Cyan
Write-Host "  ╚═══════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ── 检查 Python ────────────────────────────────────────────────────
Write-Step "检查 Python 环境..."
$pythonCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd -c "import sys; print(sys.version_info.major * 10 + sys.version_info.minor)" 2>$null
        if ($null -ne $ver -and [int]$ver -ge 38) {
            $pythonCmd = $cmd
            Write-Success "找到 Python: $cmd (版本 $(& $cmd --version 2>&1).Split(' ')[1])"
            break
        }
    } catch {}
}
if (-not $pythonCmd) {
    Write-Err "未找到 Python 3.8+，请先安装 Python: https://www.python.org/downloads/"
    Write-Host "       安装后请重新运行本脚本。" -ForegroundColor Gray
    exit 1
}

# ── 安装依赖 ──────────────────────────────────────────────────────
Write-Step "安装 Python 依赖包..."
$deps = @("Pillow", "pynput", "requests", "mcp")
$failed = @()
foreach ($pkg in $deps) {
    Write-Info "安装 $pkg..."
    try {
        & $pythonCmd -m pip install $pkg --quiet --break-system-packages 2>$null
        if ($LASTEXITCODE -ne 0) {
            & $pythonCmd -m pip install $pkg --quiet 2>$null
        }
        Write-Success "$pkg 安装完成"
    } catch {
        Write-Warn "$pkg 安装失败（可能已存在）"
    }
}

# ── 初始化配置 ─────────────────────────────────────────────────────
Write-Step "初始化配置文件..."
$configFile = Join-Path $SCRIPT_DIR "config.json"
if (-not (Test-Path $configFile)) {
    $configExample = Join-Path $SCRIPT_DIR "config.example.json"
    if (Test-Path $configExample) {
        Copy-Item $configExample $configFile
        Write-Success "已从 config.example.json 创建 config.json"
    }
}

# ── 请求 API Key ───────────────────────────────────────────────────
Write-Host ""
Write-Step "配置视觉 API Key"
Write-Info "请前往 https://console.volcengine.com/ark 创建 API Key"
Write-Info "（豆包视觉模型，火山引擎平台，免费额度足够个人使用）"
Write-Host ""

if (Test-Path $configFile) {
    $cfg = Get-Content $configFile -Raw | ConvertFrom-Json
    $currentKey = $cfg.vision_api_key
    if ($currentKey -and $currentKey -ne "your_api_key_here" -and -not $Force) {
        Write-Success "已检测到已有的 API Key: $($currentKey.Substring(0, [Math]::Min(6, $currentKey.Length)))****"
        $answer = Read-Host "是否要更新 API Key？(y/N)"
        if ($answer -ne "y" -and $answer -ne "Y") {
            Write-Info "跳过 API Key 配置"
        } else {
            $newKey = Read-Host "请输入新的 API Key"
            if ($newKey) {
                $cfg.vision_api_key = $newKey
                $cfg | ConvertTo-Json -Depth 10 | Set-Content $configFile -Encoding UTF8
                Write-Success "API Key 已更新"
            }
        }
    } else {
        Write-Info "当前 config.json 中未配置 API Key"
        $newKey = Read-Host "请输入 API Key（直接回车可稍后手动编辑 config.json）"
        if ($newKey) {
            $cfg.vision_api_key = $newKey
            $cfg | ConvertTo-Json -Depth 10 | Set-Content $configFile -Encoding UTF8
            Write-Success "API Key 已写入 config.json"
        }
    }
}

# ── 自动检测 pythonw 路径 ───────────────────────────────────────────
Write-Step "检测 pythonw.exe 路径..."
$pythonExeDir = Split-Path (Get-Command $pythonCmd).Source
$pythonwCandidates = @(
    (Join-Path $pythonExeDir "pythonw.exe"),
    (Join-Path $pythonExeDir "..\pythonw.exe"),
    "C:\Python314\pythonw.exe",
    "C:\Python312\pythonw.exe",
    "C:\Python311\pythonw.exe",
    "C:\Python310\pythonw.exe",
    "C:\Python39\pythonw.exe"
)
$pythonwPath = $null
foreach ($cand in $pythonwCandidates) {
    if (Test-Path $cand) {
        $pythonwPath = $cand
        break
    }
}
if (-not $pythonwPath) {
    Write-Warn "未找到 pythonw.exe，将使用 '$pythonCmd'"
    $pythonwPath = $pythonCmd
}
Write-Success "使用: $pythonwPath"

# 更新 config.json 中的 pythonw_path
if (Test-Path $configFile) {
    $cfg = Get-Content $configFile -Raw | ConvertFrom-Json
    $cfg.pythonw_path = $pythonwPath -replace '\\', '\\'
    $cfg | ConvertTo-Json -Depth 10 | Set-Content $configFile -Encoding UTF8
}

# ── 注册 MCP ────────────────────────────────────────────────────────
Write-Step "检查 MCP 配置..."
$mcpFile = Join-Path $SCRIPT_DIR ".mcp.json"
if (-not (Test-Path $mcpFile)) {
    Write-Info "创建 .mcp.json (MCP 工具配置)..."
    $mainPyPath = (Resolve-Path (Join-Path $SCRIPT_DIR "main.py")).Path -replace '\\', '/'
    @{
        mcpServers = @{
            "vision-proxy" = @{
                command = $pythonCmd -replace '\\', '\\'
                args = @($mainPyPath, "--mcp")
            }
        }
    } | ConvertTo-Json -Depth 10 | Set-Content $mcpFile -Encoding UTF8
    Write-Success "已创建 .mcp.json"
} else {
    Write-Info ".mcp.json 已存在，跳过"
}

# ── 生成 VBS 启动脚本 ───────────────────────────────────────────────
Write-Step "生成启动脚本..."
$mainPyPath = (Resolve-Path (Join-Path $SCRIPT_DIR "main.py")).Path
$vbsContent = "CreateObject(`"WScript.Shell`").Run `"$pythonwPath $mainPyPath`", 0, False"
$vbsPath = Join-Path $SCRIPT_DIR "start_vision_proxy.vbs"
$vbsContent | Set-Content $vbsPath -Encoding UTF8
Write-Success "已生成: start_vision_proxy.vbs"

# ── 注册开机自启动 ─────────────────────────────────────────────────
Write-Host ""
Write-Step "注册开机自启动（复制到用户启动文件夹）..."

$startupDir = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup"
$vbsFile = "start_vision_proxy.vbs"
$localVbs = Join-Path $SCRIPT_DIR $vbsFile
$destVbs = Join-Path $startupDir $vbsFile

if ($Uninstall) {
    if (Test-Path $destVbs) {
        Remove-Item $destVbs -Force
        Write-Success "已取消开机自启动（已删除启动文件夹中的 $vbsFile）"
    } else {
        Write-Info "开机自启动未注册，无需取消"
    }
} else {
    if (Test-Path $destVbs) {
        Write-Info "开机自启动已注册: $destVbs"
        if (-not $Force) {
            $answer = Read-Host "是否重新注册？(y/N)"
            if ($answer -ne "y" -and $answer -ne "Y") {
                Write-Info "跳过"
            } else {
                Remove-Item $destVbs -Force
            }
        } else {
            Remove-Item $destVbs -Force
        }
    }

    if (-not (Test-Path $destVbs)) {
        try {
            Copy-Item $localVbs $destVbs -Force
            Write-Success "已注册开机自启动"
            Write-Info "  启动文件: $destVbs"
        } catch {
            Write-Warn "复制到启动文件夹失败: $_"
            Write-Info "请手动复制 $localVbs 到 $startupDir"
        }
    }
}

# ── 完成 ───────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  ╔═══════════════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "  ║   安装完成！                                       ║" -ForegroundColor Green
Write-Host "  ╚═══════════════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
Write-Info "使用方式："
Write-Info "  1. 登录 Windows 后程序自动在后台运行"
Write-Info "  2. 复制任意图片，按 Ctrl+Alt+V"
Write-Info "  3. AI 自动识别图片内容并返回文字描述"
Write-Host ""
Write-Info "手动控制："
Write-Info "  启动服务: python main.py"
Write-Info "  卸载自启: .\setup.ps1 -Uninstall"
Write-Info "  启动文件夹: shell:startup"
Write-Host ""
Write-Host "提示：首次使用前请确认 config.json 中已填入有效的 API Key" -ForegroundColor Yellow
Write-Host ""

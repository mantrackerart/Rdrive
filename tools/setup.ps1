# Rdrive - Bo cai dat tu dong & tu sua loi (may thieu gi tu tai).
# Khong can cai san Python hay rclone: tu mang theo Python 3.12 (runtime\), PySide6 va rclone (bin\).
param([switch]$NoPause)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root    = Split-Path -Parent $PSScriptRoot
$Runtime = Join-Path $Root "runtime"
$Py      = Join-Path $Runtime "python.exe"
$PyW     = Join-Path $Runtime "pythonw.exe"
$Bin     = Join-Path $Root "bin"
$Data    = Join-Path $Root "data"
$Logs    = Join-Path $Root "logs"
$PyVer   = "3.12.10"

New-Item -ItemType Directory -Force -Path $Logs, $Data, $Bin | Out-Null
$Log = Join-Path $Logs "setup.log"
Start-Transcript -Path $Log -Append | Out-Null

function Step($n, $text) { Write-Host ""; Write-Host "[$n/5] $text" -ForegroundColor Cyan }
function Ok($text)       { Write-Host "      OK  $text" -ForegroundColor Green }
function Warn($text)     { Write-Host "      !!  $text" -ForegroundColor Yellow }

function Download($url, $dest) {
    Write-Host "      tai: $url"
    $curl = Join-Path $env:SystemRoot "System32\curl.exe"
    if (Test-Path $curl) {
        & $curl -L --fail --retry 3 --retry-delay 2 -o $dest $url
        if ($LASTEXITCODE -ne 0) { throw "curl loi ma $LASTEXITCODE khi tai $url" }
    } else {
        Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
    }
}

function Test-Python {
    if (-not (Test-Path $Py)) { return $false }
    $ErrorActionPreference = "Continue"
    try {
        & $Py -c "import sys, site; print(sys.version)" *> $null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Find-ExistingRclone {
    $localBin = Join-Path $Bin "rclone.exe"
    if (Test-Path $localBin) { return $localBin }
    $cmd = Get-Command rclone -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    if ($env:LOCALAPPDATA) {
        $wg = Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\Rclone.Rclone*") -Recurse -Filter "rclone.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($wg) { return $wg.FullName }
    }
    return ""
}

function Fail($text) {
    Write-Host ""
    Write-Host "!!! CAI DAT BI LOI: $text" -ForegroundColor Red
    Write-Host "    Chi tiet da luu tai: $Log"
    try { Stop-Transcript | Out-Null } catch {}
    if (-not $NoPause) { Read-Host "Bam Enter de dong" | Out-Null }
    exit 1
}

Write-Host "=========================================================="
Write-Host "  Rdrive - Cai dat tu dong (may thieu gi tu tai)"
Write-Host "  Thu muc: $Root"
Write-Host "=========================================================="

try {
    # ------------------------------------------------------------ 1. Python runtime
    Step 1 "Python rieng cua Rdrive (runtime\)"
    if (Test-Python) {
        Ok ("da co: " + (& $Py -c "import sys; print(sys.version.split()[0])"))
    } else {
        if (Test-Path $Runtime) {
            Warn "runtime\ bi hong -> cai lai"
            Remove-Item -Recurse -Force $Runtime
        }
        $tmp = Join-Path $env:TEMP ("rdrive_py_" + [guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Force -Path $tmp, $Runtime | Out-Null
        $zip = Join-Path $tmp "python.zip"
        Download "https://www.python.org/ftp/python/$PyVer/python-$PyVer-embed-amd64.zip" $zip
        Expand-Archive -Path $zip -DestinationPath $Runtime -Force

        $pth = Get-ChildItem -Path $Runtime -Filter "python*._pth" | Select-Object -First 1
        if (-not $pth) { throw "Khong tim thay file ._pth trong goi Python" }
        $zipName = (Get-ChildItem -Path $Runtime -Filter "python3*.zip" | Select-Object -First 1).Name
        Set-Content -Path $pth.FullName -Encoding ASCII -Value @($zipName, ".", "..", "Lib\site-packages", "import site")

        $getpip = Join-Path $tmp "get-pip.py"
        Download "https://bootstrap.pypa.io/get-pip.py" $getpip
        & $Py $getpip --no-warn-script-location --disable-pip-version-check
        if ($LASTEXITCODE -ne 0) { throw "Khong cai duoc pip" }

        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
        if (-not (Test-Python)) { throw "Python vua cai khong chay duoc" }
        Ok "da cai Python $PyVer vao runtime\"
    }

    # ------------------------------------------------------------ 2. Thu vien (PySide6)
    Step 2 "Thu vien giao dien (PySide6-Essentials)"
    $ErrorActionPreference = "Continue"
    & $Py -c "import PySide6.QtWidgets, PySide6.QtNetwork" *> $null
    $hasQt = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = "Stop"
    if ($hasQt) {
        Ok ("da co: PySide6 " + (& $Py -c "import PySide6; print(PySide6.__version__)"))
    } else {
        & $Py -m pip install --disable-pip-version-check --no-warn-script-location -q --upgrade pip
        & $Py -m pip install --disable-pip-version-check --no-warn-script-location -r (Join-Path $Root "requirements.txt")
        if ($LASTEXITCODE -ne 0) { throw "pip khong cai duoc PySide6-Essentials" }
        & $Py -c "import PySide6.QtWidgets, PySide6.QtNetwork; print('PySide6 OK')"
        if ($LASTEXITCODE -ne 0) { throw "PySide6 da cai nhung khong nap duoc" }
        Ok "da cai PySide6-Essentials"
    }

    # ------------------------------------------------------------ 3. rclone.exe
    Step 3 "Kiem tra / tai rclone.exe"
    $rcFound = Find-ExistingRclone
    if ($rcFound) {
        Ok ("da co rclone: " + $rcFound)
    } else {
        $tmp = Join-Path $env:TEMP ("rdrive_rc_" + [guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Force -Path $tmp | Out-Null
        $zip = Join-Path $tmp "rclone.zip"
        Download "https://downloads.rclone.org/rclone-current-windows-amd64.zip" $zip
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        $exe = Get-ChildItem -Path $tmp -Recurse -Filter "rclone.exe" | Select-Object -First 1
        if (-not $exe) { throw "Khong tim thay rclone.exe trong goi tai ve" }
        Copy-Item $exe.FullName (Join-Path $Bin "rclone.exe") -Force
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
        Ok "da tai rclone.exe vao bin\"
    }

    # ------------------------------------------------------------ 4. Kiem tra nap Rdrive
    Step 4 "Kiem tra nap Rdrive"
    Push-Location $Root
    & $Py -c "import rdrive.app; from rdrive import __version__; print('Rdrive', __version__)"
    $code = $LASTEXITCODE
    Pop-Location
    if ($code -ne 0) { throw "Rdrive khong nap duoc (xem loi phia tren)" }
    Ok "Rdrive san sang"

    # ------------------------------------------------------------ 5. Shortcut Desktop + Start Menu
    Step 5 "Tao Shortcut (Desktop + Start Menu)"
    $sh = New-Object -ComObject WScript.Shell
    $targets = @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))
    foreach ($dir in $targets) {
        if (-not (Test-Path $dir)) { continue }
        $lnk = $sh.CreateShortcut((Join-Path $dir "Rdrive.lnk"))
        $lnk.TargetPath = $PyW
        $lnk.Arguments = '"' + (Join-Path $Root "Rdrive.pyw") + '"'
        $lnk.WorkingDirectory = $Root
        $lnk.Description = "Rdrive - Quan ly & Backup Google Drive (rclone)"
        $lnk.Save()
    }
    Ok "da tao shortcut Rdrive ngoai Desktop va Start Menu"

} catch {
    Fail $_.Exception.Message
}

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Green
Write-Host "  HOAN TAT! Mo Rdrive bang shortcut 'Rdrive' ngoai Desktop" -ForegroundColor Green
Write-Host "  hoac bam dup file Rdrive.bat." -ForegroundColor Green
Write-Host "  Log cai dat: $Log" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
try { Stop-Transcript | Out-Null } catch {}
if (-not $NoPause) { Read-Host "Bam Enter de dong" | Out-Null }
exit 0

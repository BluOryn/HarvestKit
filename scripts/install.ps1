<#
.SYNOPSIS
  Set HarvestKit up on a Windows machine that has nothing installed.

.DESCRIPTION
  This is the script for a laptop with no Python, no Git and nobody who wants to
  learn either. It:

    1. Finds a usable Python, or downloads and installs one for this user only
       (no administrator rights needed, nothing added to PATH that could upset
       anything else on the machine).
    2. Builds the project's own virtualenv so HarvestKit's dependencies never
       collide with anything else.
    3. Installs the dependencies, and the browser the stealth rung uses.
    4. Writes a desktop shortcut called "HarvestKit" that opens the control
       panel. After this, the whole tool is that one icon.
    5. Proves the install by actually reading a dozen European websites, which
       is the only check that distinguishes "it installed" from "it works here".

  Safe to re-run: every step is skipped if it is already done.

.EXAMPLE
  .\scripts\install.ps1
  .\scripts\install.ps1 -SkipBrowser     # no stealth browser (saves ~400 MB)
  .\scripts\install.ps1 -NoShortcut
#>
[CmdletBinding()]
param(
    # Skip `playwright install chromium`. The transport ladder still has its
    # first two rungs, which handle the large majority of sites.
    [switch] $SkipBrowser,
    # Do not create the desktop shortcut.
    [switch] $NoShortcut,
    # Do not run the live network check at the end.
    [switch] $SkipCheck
)

$ErrorActionPreference = "Stop"

# Windows PowerShell 5.1 turns native stderr into terminating errors when it is
# piped with 2>&1 under $ErrorActionPreference='Stop'. pip and playwright both
# write progress to stderr, so without this the install dies on the first line
# of output it produces.
function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)] [string]   $Exe,
        [Parameter(Mandatory = $true)] [string[]] $Arguments,
        [switch] $Quiet
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if ($Quiet) { & $Exe @Arguments 2>&1 | Out-Null }
        else        { & $Exe @Arguments 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray } }
    }
    finally {
        $ErrorActionPreference = $previous
    }
    return $LASTEXITCODE
}

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Ok($text)   { Write-Host "    $text" -ForegroundColor Green }
function Warn($text) { Write-Host "    $text" -ForegroundColor Yellow }
function Die($text)  { Write-Host "`n!!! $text" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "  HarvestKit installer" -ForegroundColor White
Write-Host "  ----------------------------------------"
Write-Host "  This takes about five minutes and needs no administrator rights."

# --------------------------------------------------------------- 1. Python
$MinimumPython = [Version]"3.10"
$PythonUrl = "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"

function Find-Python {
    foreach ($candidate in @("py -3.12", "py -3.11", "py -3", "python3", "python")) {
        $parts = $candidate.Split(" ")
        $exe = $parts[0]
        $args = if ($parts.Length -gt 1) { $parts[1..($parts.Length - 1)] } else { @() }
        try {
            $output = & $exe @args -c "import sys;print('%d.%d' % sys.version_info[:2]);print(sys.executable)" 2>$null
            if ($LASTEXITCODE -ne 0 -or -not $output) { continue }
            if ([Version]$output[0] -ge $MinimumPython) { return $output[1] }
        }
        catch { continue }
    }
    return $null
}

Step "Looking for Python"
$python = Find-Python
if ($python) {
    Ok "Found $python"
}
else {
    Warn "No Python 3.10+ on this machine. Downloading one (about 25 MB)."
    $installer = Join-Path $env:TEMP "python-harvestkit.exe"
    try {
        # TLS 1.2 is not the default on stock 5.1, and python.org refuses anything older.
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $PythonUrl -OutFile $installer -UseBasicParsing
    }
    catch { Die "Could not download Python: $_`n    Install it yourself from python.org and run this again." }

    Warn "Installing Python for your user account only. This takes a minute."
    # InstallAllUsers=0 keeps it out of Program Files and needs no elevation.
    # PrependPath=0 leaves the machine's existing PATH untouched: this install
    # is for HarvestKit, and silently becoming the system `python` is how an
    # unrelated tool breaks a week later.
    $arguments = @("/quiet", "InstallAllUsers=0", "PrependPath=0", "Include_launcher=1",
                   "Include_test=0", "Include_doc=0", "SimpleInstall=1")
    $process = Start-Process -FilePath $installer -ArgumentList $arguments -Wait -PassThru
    if ($process.ExitCode -ne 0) { Die "The Python installer failed with code $($process.ExitCode)." }
    Remove-Item $installer -ErrorAction SilentlyContinue

    $python = Find-Python
    if (-not $python) {
        $guess = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
        if (Test-Path $guess) { $python = $guess }
    }
    if (-not $python) { Die "Python installed but could not be found. Restart this window and run the script again." }
    Ok "Installed $python"
}

# ------------------------------------------------------------- 2. virtualenv
Step "Setting up the project's own Python environment"
$venv = Join-Path $root ".venv"
$venvPython = Join-Path $venv "Scripts\python.exe"
if (Test-Path $venvPython) {
    Ok "Already there."
}
else {
    Invoke-Native -Exe $python -Arguments @("-m", "venv", $venv) | Out-Null
    if (-not (Test-Path $venvPython)) { Die "Could not create the environment in $venv." }
    Ok "Created $venv"
}

# ----------------------------------------------------------- 3. dependencies
Step "Installing the dependencies"
Invoke-Native -Exe $venvPython -Arguments @("-m", "pip", "install", "--upgrade", "pip", "--quiet") | Out-Null
$code = Invoke-Native -Exe $venvPython -Arguments @("-m", "pip", "install", "-r", "requirements.txt", "--quiet")
if ($code -ne 0) { Die "Dependency install failed. Scroll up for the reason." }
Ok "Done."

Step "Installing the stealth browser fork (optional but recommended)"
$code = Invoke-Native -Exe $venvPython -Arguments @("-m", "pip", "install", "patchright", "--quiet")
if ($code -ne 0) { Warn "patchright would not install. The first two transport rungs still work." }
else { Ok "Installed." }

if (-not $SkipBrowser) {
    Step "Downloading Chromium for the stealth rung (about 150 MB, once)"
    $code = Invoke-Native -Exe $venvPython -Arguments @("-m", "patchright", "install", "chromium")
    if ($code -ne 0) {
        $code = Invoke-Native -Exe $venvPython -Arguments @("-m", "playwright", "install", "chromium")
    }
    if ($code -ne 0) { Warn "The browser did not install. Everything except the last transport rung still works." }
    else { Ok "Ready." }
}

# ------------------------------------------------------------- 4. shortcut
Step "Making the HarvestKit shortcut"
$launcher = Join-Path $root "HarvestKit.bat"
@"
@echo off
rem  Opens the HarvestKit control panel in your browser.
rem  Close this window to shut the panel down.
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  ".venv\Scripts\python.exe" run_panel.py %*
) else (
  python run_panel.py %*
)
if errorlevel 1 (
  echo.
  echo HarvestKit could not start. Run scripts\install.ps1 again.
  pause
)
"@ | Set-Content -Path $launcher -Encoding ASCII
Ok "Wrote $launcher"

if (-not $NoShortcut) {
    try {
        $desktop = [Environment]::GetFolderPath("Desktop")
        $shortcutPath = Join-Path $desktop "HarvestKit.lnk"
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = $launcher
        $shortcut.WorkingDirectory = $root
        $shortcut.Description = "Open the HarvestKit control panel"
        $shortcut.IconLocation = "$env:SystemRoot\System32\SHELL32.dll,13"
        $shortcut.Save()
        Ok "Desktop shortcut created: $shortcutPath"
    }
    catch { Warn "Could not create the desktop shortcut: $_" }
}

# ---------------------------------------------------------------- 5. proof
if (-not $SkipCheck) {
    Step "Checking this machine can actually read European websites"
    Write-Host "    (This is the check that matters. 'pip install worked' and 'this laptop"
    Write-Host "     can scrape' are different claims.)" -ForegroundColor DarkGray
    Invoke-Native -Exe $venvPython -Arguments @("tools\check_egress.py") | Out-Null
}

Write-Host ""
Write-Host "  Done." -ForegroundColor Green
Write-Host "  Double-click the HarvestKit icon on your desktop to start."
Write-Host ""

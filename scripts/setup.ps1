<#
.SYNOPSIS
  First-time setup. Run once after cloning; never again.

.DESCRIPTION
  Creates the virtualenv, installs the dependencies, and then proves the install
  actually works by harvesting a handful of real leads. That last step is the
  point: "pip install succeeded" and "this machine can scrape" are different
  claims, and only the second one matters tomorrow morning.

.EXAMPLE
  .\scripts\setup.ps1
  .\scripts\setup.ps1 -SkipSmokeTest
#>
[CmdletBinding()]
param(
    # Skip the live 5-minute proof at the end. Not recommended on a new machine.
    [switch] $SkipSmokeTest
)

$ErrorActionPreference = "Stop"

# Windows PowerShell 5.1 -- still the default shell on a stock Windows box --
# turns every stderr line of a native command into an ErrorRecord when it is
# piped through `2>&1`. With $ErrorActionPreference = 'Stop' that first record
# is a terminating NativeCommandError, so the script died on the very first
# `seed:` line the run logged: no CSV, no verification, exit 1 instead of the
# documented 0/2/3. Python's logging writes to stderr by default, so this fired
# every single time on 5.1 and never once on pwsh 7.
function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)] [string]   $Exe,
        [Parameter(Mandatory = $true)] [string[]] $Arguments,
        [string] $TeeTo = "",
        [scriptblock] $Filter = $null
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if ($TeeTo) {
            & $Exe @Arguments 2>&1 | Tee-Object -FilePath $TeeTo
        }
        elseif ($Filter) {
            & $Exe @Arguments 2>&1 | Where-Object $Filter
        }
        else {
            & $Exe @Arguments 2>&1
        }
    }
    finally {
        $ErrorActionPreference = $previous
    }
    return $LASTEXITCODE
}

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Ok($text)   { Write-Host "    $text"   -ForegroundColor Green }
function Warn($text) { Write-Host "    $text"   -ForegroundColor Yellow }

Step "Checking Python"
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { throw "Python is not on PATH. Install 3.10+ from python.org and tick 'Add to PATH'." }
$version = (& python -c "import sys; print('%d.%d' % sys.version_info[:2])")
if ([version]$version -lt [version]"3.10") { throw "Python $version found; 3.10 or newer is required." }
Ok "Python $version"

Step "Creating the virtualenv (.venv)"
if (Test-Path ".venv") {
    Ok "already exists, reusing it"
} else {
    & python -m venv .venv
    Ok "created"
}
$python = Join-Path $root ".venv/Scripts/python.exe"

Step "Installing dependencies"
& $python -m pip install --upgrade pip --quiet
& $python -m pip install -r requirements.txt --quiet
Ok "installed"

Step "Checking the install"
& $python -c @"
import sys
sys.path.insert(0, 'src')
import requests, bs4, lxml, yaml, dns.resolver  # noqa: F401
from leadgen.cli import build_parser
from job_scraper.config import load_config, resolve_config_path
load_config(resolve_config_path('configs/leads/swiss-it.yaml'))
build_parser().parse_args(['--config', 'x'])
print('    imports, config and CLI all load')
"@
Ok "ready"

Step "Checking outbound port 25 (mailbox verification)"
# Every home network, most offices and every major cloud provider block outbound
# 25. Knowing which side of that line this machine sits on decides whether the
# daily run needs --no-smtp, and finding out now beats finding out at hour three.
$smtpOpen = $false
try {
    $probe = New-Object System.Net.Sockets.TcpClient
    $smtpOpen = $probe.ConnectAsync("gmail-smtp-in.l.google.com", 25).Wait(5000)
    $probe.Close()
} catch { $smtpOpen = $false }
if ($smtpOpen) {
    Ok "open — leave -NoSmtp off and addresses get verified properly"
} else {
    Warn "blocked — always pass -NoSmtp. Addresses will be inferred, not verified."
}

if (-not $SkipSmokeTest) {
    Step "Proving it works (real run, ~5 minutes)"
    Write-Host "    Harvesting 2 pages of jobs.ch for a handful of real leads." -ForegroundColor DarkGray
    $smokeArgs = @(
        "run_leads.py", "--config", "configs/leads/swiss-it.yaml",
        "--jobsch-pages", "2", "--countries", "CH", "--target", "10", "--overfetch", "0",
        "--checkpoint", ".cache/setup-check.sqlite", "--output", "output/setup-check.csv"
    )
    if (-not $smtpOpen) { $smokeArgs += "--no-smtp" }
    Invoke-Native -Exe $python -Arguments $smokeArgs -Filter { $_ -notmatch "WARNING Retrying" } | Select-Object -Last 12
    if (-not (Test-Path "output/setup-check.csv")) { throw "Smoke run produced no file. Setup is not complete." }
    & $python tools/verify_leads.py output/setup-check.csv
    if ($LASTEXITCODE -ne 0) { throw "Smoke run produced a file that fails verification." }
    Ok "real leads harvested and verified"
}

Write-Host "`nSetup complete." -ForegroundColor Green
$command = if ($smtpOpen) { ".\scripts\daily.ps1" } else { ".\scripts\daily.ps1 -NoSmtp" }
Write-Host "Run the harvest with:  $command"
Write-Host "Read docs\ONBOARDING.md for what the numbers mean."

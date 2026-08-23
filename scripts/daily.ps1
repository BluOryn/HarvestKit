<#
.SYNOPSIS
  One day's lead harvest. Run this, get a dated CSV of people nobody has been sent before.

.DESCRIPTION
  Everything about a daily operation lives in two decisions, and both are made here
  so the operator never has to remember them:

    * ONE checkpoint, forever. It is the memory. `companies` stops today spending
      hours re-crawling employers yesterday already did; `delivered` stops today's
      file being yesterday's file with a new date on it. Deleting it means starting
      over and re-sending everyone.

    * ONE dated output per day. Nothing is overwritten, so a bad morning can be
      inspected instead of guessed at.

  Safe to re-run. If it dies at hour two, run it again -- the checkpoint resumes,
  and nothing is marked delivered until the CSV actually exists on disk.

.EXAMPLE
  .\scripts\daily.ps1
  .\scripts\daily.ps1 -Target 400 -Pages 65
  .\scripts\daily.ps1 -Roles any -Target 3000
#>
[CmdletBinding()]
param(
    # Rows wanted today. The run stops short and says so rather than padding.
    [int]    $Target     = 300,
    # jobs.ch pages to walk, 22 postings each. 65 covers a 30-day window.
    [int]    $Pages      = 65,
    # How old a posting may be, in days. 0 = decide from the checkpoint: 30 on the
    # first run to sweep the whole board, 3 afterwards. Measured against the live
    # board: 1 day = 25 postings, 3 = 185, 7 = 478, 14 = 787, 30 = 1396. Asking for
    # 30 every morning re-fetches thirteen hundred postings to rediscover employers
    # that were crawled yesterday.
    [int]    $Days       = 0,
    # Free-text keyword added to the filter. Empty means the whole sector.
    [string] $Term       = "",
    # jobs.ch category ids. 106 IT/Telecom, 146 Engineering/Technical,
    # 156 Management/Consulting, 167 Electronics/Electrotechnics.
    [string] $Categories = "106,146,156,167",
    # "hr,tech_leadership,executive" for decision makers, "any" for every named person.
    [string] $Roles      = "hr,tech_leadership,executive",
    [string] $Countries  = "CH",
    # Revisit an employer this many days after it was last crawled. Staff change.
    [double] $RecrawlAfter = 30,
    # The jobs.ch search whose filter defines the sector and recency window. Build
    # the search you want in a browser and paste the address bar here. Empty means
    # the shipped IT filter.
    [string] $JobschUrl = "",
    # Skip mailbox probing. Required wherever outbound port 25 is blocked, which is
    # most home and office networks and every major cloud provider.
    [switch] $NoSmtp,
    # Read the commercial register for companies that name nobody. Needs ZEFIX_USER
    # and ZEFIX_PASSWORD; without them the run warns and carries on.
    [switch] $Register
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$today  = Get-Date -Format "yyyy-MM-dd"
$outDir = Join-Path $root "output"
$logDir = Join-Path $root "logs"
foreach ($dir in @($outDir, $logDir)) {
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
}

$checkpoint = Join-Path $root ".cache/daily.sqlite"
$output     = Join-Path $outDir "leads-$today.csv"
$logFile    = Join-Path $logDir "run-$today.log"

$python = Join-Path $root ".venv/Scripts/python.exe"
if (-not (Test-Path $python)) { $python = "python" }

# A fresh checkpoint has never seen an employer, so the first run should sweep the
# whole board. Every run after that only needs what appeared since.
if ($Days -le 0) {
    $Days = if (Test-Path $checkpoint) { 3 } else { 30 }
    Write-Host "  window     : last $Days days (auto)"
} else {
    Write-Host "  window     : last $Days days"
}

$leadArgs = @(
    "run_leads.py",
    "--config", "configs/leads/swiss-it.yaml",
    "--jobsch-pages", $Pages,
    "--countries", $Countries,
    "--roles", $Roles,
    "--target", $Target,
    # No early stop. The default (target x 3 *email-bearing* leads, counted across
    # every role) trips long before enough target-role people accumulate, and the
    # crawl quits before reaching the companies that had them.
    "--overfetch", "0",
    "--recrawl-after", $RecrawlAfter,
    "--jobsch-days", $Days,
    "--jobsch-categories", $Categories,
    "--only-new",
    "--checkpoint", $checkpoint,
    "--output", $output
)
if ($Term)      { $leadArgs += @("--jobsch-term", $Term) }
if ($JobschUrl) { $leadArgs += @("--jobsch-url", $JobschUrl) }
if ($NoSmtp)    { $leadArgs += "--no-smtp" }
if ($Register)  { $leadArgs += "--register" }

Write-Host "HarvestKit daily run - $today" -ForegroundColor Cyan
Write-Host "  checkpoint : $checkpoint"
Write-Host "  output     : $output"
Write-Host "  log        : $logFile"
Write-Host ""

& $python @leadArgs 2>&1 | Tee-Object -FilePath $logFile
$code = $LASTEXITCODE

Write-Host ""
if ($code -eq 0 -or $code -eq 2) {
    # Gate the file before anyone sees it. The verifier exits non-zero only when a
    # hard guarantee is broken (a row missing a name, an address, a company or a
    # country); everything else it prints is a quality signal for a human.
    Write-Host "Checking the file..." -ForegroundColor Cyan
    & $python (Join-Path $root "tools/verify_leads.py") $output
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "DO NOT SEND THIS FILE. It failed verification above." -ForegroundColor Red
        exit 3
    }
}

if ($code -eq 0) {
    Write-Host "Done. $output" -ForegroundColor Green
} elseif ($code -eq 2) {
    # Not a failure. The supply ran out before the target did, which on a daily
    # run is the normal state once the first full sweep is behind you.
    Write-Host "Done, but short of $Target rows. See the SHORTFALL line above." -ForegroundColor Yellow
} else {
    Write-Host "Run failed (exit $code). Full log: $logFile" -ForegroundColor Red
}
exit $code

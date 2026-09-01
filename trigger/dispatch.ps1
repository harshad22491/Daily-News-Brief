# Fires the Daily News Brief workflow via GitHub's workflow_dispatch API.
# Production uses cron-job.org (see docs/runbook.md); this is the equivalent for a
# Windows Task Scheduler task at 07:15 IST:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\daily-news-brief\dispatch.ps1
# Requires: the PAT stored in the file below (one line), readable only by the task's account.
$TokenFile = Join-Path $env:USERPROFILE ".daily_news_brief_gh_token"
$Repo      = "harshad22491/Daily-News-Brief"
$LogFile   = Join-Path (Split-Path $PSCommandPath) "dispatch.log"

$stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
try {
    $token = (Get-Content $TokenFile -Raw).Trim()
    $headers = @{
        "Authorization"        = "Bearer $token"
        "Accept"               = "application/vnd.github+json"
        "X-GitHub-Api-Version" = "2022-11-28"
    }
    Invoke-RestMethod -Method Post -Uri "https://api.github.com/repos/$Repo/actions/workflows/daily.yml/dispatches" `
        -Headers $headers -ContentType "application/json" -Body '{"ref":"main"}' | Out-Null
    "$stamp dispatched OK" | Add-Content $LogFile
    exit 0
} catch {
    "$stamp dispatch FAILED: $($_.Exception.Message)" | Add-Content $LogFile
    exit 1
}

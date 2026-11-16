# Launch JFLove inside the guest as the interactive user and expose CDP.
# NOTE: keep this file ASCII-only. PowerShell 5.1 reads .ps1 as ANSI when there is no BOM,
# and a Chinese comment can eat the following newline and break parsing (hit on 2026-11-16).
$ErrorActionPreference = 'Continue'
New-Item 'C:\jf' -ItemType Directory -Force | Out-Null
netsh interface portproxy delete v4tov4 listenport=9223 listenaddress=0.0.0.0 2>$null | Out-Null
netsh interface portproxy add v4tov4 listenport=9223 listenaddress=0.0.0.0 connectport=9222 connectaddress=127.0.0.1 | Out-Null
Start-Service iphlpsvc -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName 'jf-cdp' -Direction Inbound -Protocol TCP -LocalPort 9223 -Action Allow -ErrorAction SilentlyContinue | Out-Null

# Wipe per-user client state INSIDE the jf session: QGA exec runs as SYSTEM in Session 0,
# so %APPDATA% there is the SYSTEM profile, not jf's (that mistake cost a full rerun).
# Build the .cmd from an array: a long escaped double-quoted string produced a 0-byte file.
$cmd = (@(
    '@echo off',
    'set QTWEBENGINE_REMOTE_DEBUGGING=9222',
    'set QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu',
    'rd /s /q "%APPDATA%\JFLove" 2>nul',
    'rd /s /q "%LOCALAPPDATA%\JFLove" 2>nul',
    'start "" "C:\Program Files\JFLove\JFLove.exe"'
) -join "`r`n")
Set-Content -Path 'C:\jf\run-app.cmd' -Value $cmd -Encoding ASCII
'cmd-bytes: ' + (Get-Item 'C:\jf\run-app.cmd').Length

schtasks /delete /tn JFApp /f 2>$null | Out-Null
schtasks /create /tn JFApp /tr 'C:\jf\run-app.cmd' /sc once /st 23:59 /ru jf /rl highest /it /f | Out-Null
schtasks /run /tn JFApp | Out-Null
Start-Sleep -Seconds 2
'portproxy:'; netsh interface portproxy show v4tov4

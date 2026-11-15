# 在客机里以交互用户身份启动 JFLove，并暴露 CDP 端口（宿主侧 9223 → 客机 127.0.0.1:9222）
$ErrorActionPreference = 'Continue'
New-Item 'C:\jf' -ItemType Directory -Force | Out-Null
netsh interface portproxy delete v4tov4 listenport=9223 listenaddress=0.0.0.0 2>$null | Out-Null
netsh interface portproxy add v4tov4 listenport=9223 listenaddress=0.0.0.0 connectport=9222 connectaddress=127.0.0.1 | Out-Null
Start-Service iphlpsvc -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName 'jf-cdp' -Direction Inbound -Protocol TCP -LocalPort 9223 -Action Allow -ErrorAction SilentlyContinue | Out-Null
$cmd = "@echo off`r`nset QTWEBENGINE_REMOTE_DEBUGGING=9222`r`nset QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu`r`nstart `"`" `"C:\Program Files\JFLove\JFLove.exe`"`r`n"
Set-Content -Path 'C:\jf\run-app.cmd' -Value $cmd -Encoding ASCII
schtasks /delete /tn JFApp /f 2>$null | Out-Null
schtasks /create /tn JFApp /tr 'C:\jf\run-app.cmd' /sc once /st 23:59 /ru jf /rl highest /it /f | Out-Null
schtasks /run /tn JFApp | Out-Null
Start-Sleep -Seconds 2
'portproxy:'; netsh interface portproxy show v4tov4

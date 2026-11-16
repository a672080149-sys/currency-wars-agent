$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$host.UI.RawUI.WindowTitle = 'cw-bridge'
$py = Join-Path $PSScriptRoot '..\vendor\March7thAssistant\.venv\Scripts\python.exe'
$server = Join-Path $PSScriptRoot 'server.py'
Set-Location $PSScriptRoot
& $py -X utf8 $server
Write-Host ''
Write-Host "cw-bridge 已退出（代码 $LASTEXITCODE），按回车关闭窗口"
Read-Host | Out-Null

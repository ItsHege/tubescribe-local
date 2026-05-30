$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
Start-Process pythonw -ArgumentList ".\\launch_tool.pyw"

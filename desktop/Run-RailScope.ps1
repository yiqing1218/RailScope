$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Start-Process -FilePath 'D:\Python312\python.exe' -ArgumentList 'launcher.py' -WorkingDirectory $scriptRoot

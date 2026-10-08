# cluster-prism-lite UCFS v1.0 launcher
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir
python main.py @args
exit $LASTEXITCODE

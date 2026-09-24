param(
  [int]$Port = 8765
)

$ProjectRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
Set-Location -LiteralPath $ProjectRoot
$env:PYTHONDONTWRITEBYTECODE = "1"
python -B app\server.py --port $Port @args

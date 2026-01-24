$root = "views"
$out  = Join-Path $env:TEMP "results_views_dump.txt"

Remove-Item $out -ErrorAction SilentlyContinue

$files = Get-ChildItem $root -Recurse -File -Filter "*.py" |
  Where-Object { $_.FullName -notmatch "\\__pycache__\\" } |
  Sort-Object FullName

$sb = New-Object System.Text.StringBuilder

foreach ($f in $files) {
  $rel = $f.FullName.Replace((Get-Location).Path + "\", "")
  [void]$sb.Append("`r`n`r`n# PATH: $rel`r`n`r`n")
  [void]$sb.Append([System.IO.File]::ReadAllText($f.FullName, [System.Text.Encoding]::UTF8))
}

# ✅ UTF-8로 저장
[System.IO.File]::WriteAllText($out, $sb.ToString(), [System.Text.Encoding]::UTF8)

Write-Host "✅ dump saved:" $out
notepad $out

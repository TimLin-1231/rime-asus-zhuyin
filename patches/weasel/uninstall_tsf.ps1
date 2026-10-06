# Restore the original Weasel 0.17.4 TSF DLLs. Run elevated.
# The originals were kept next to the patched files as *.orig-<stamp>
# (and copied to D:\Document\輸入法\backups\20261006-131301-weasel-tsf).
$ErrorActionPreference = 'Stop'
$dir = 'C:\Program Files\Rime\weasel-0.17.4'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
foreach ($name in 'weasel.dll', 'weaselx64.dll') {
  $orig = Get-ChildItem $dir -Filter "$name.orig-*" | Sort-Object Name | Select-Object -First 1
  if (-not $orig) { throw "no $name.orig-* in $dir" }
  Rename-Item (Join-Path $dir $name) "$name.patched-$stamp"
  Copy-Item $orig.FullName (Join-Path $dir $name)
}

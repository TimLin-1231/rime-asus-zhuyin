# Put the original Weasel TSF DLLs back (undo install_tsf_system.ps1).
# Run elevated, from 64-bit PowerShell. The OLDEST backup of each file is the
# stock DLL; the patched file is renamed out of the way (it may be in use)
# and the stock one copied in. Applications use it after they restart.
param([string]$Log = 'D:\src\weasel\install_tsf_system.log')
$ErrorActionPreference = 'Stop'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
"uninstall $stamp" | Out-File $Log -Append -Encoding utf8
try {
  if (-not [Environment]::Is64BitProcess) { throw 'run this from 64-bit PowerShell' }
  foreach ($target in "$env:windir\System32\weasel.dll", "$env:windir\SysWOW64\weasel.dll") {
    $orig = Get-ChildItem "$target.orig-*" | Sort-Object Name | Select-Object -First 1
    if (-not $orig) { throw "no backup of $target" }
    Rename-Item $target "$target.patched-$stamp"
    Copy-Item $orig.FullName $target
    "$target <- $($orig.Name)" | Out-File $Log -Append -Encoding utf8
  }
  'done' | Out-File $Log -Append -Encoding utf8
} catch {
  "FAILED: $($_.Exception.Message)" | Out-File $Log -Append -Encoding utf8
  exit 1
}

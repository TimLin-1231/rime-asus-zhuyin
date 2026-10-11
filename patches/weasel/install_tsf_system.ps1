# Install the patched Weasel TSF DLLs where Windows really loads them from.
#
# The text service is registered as C:\Windows\System32\weasel.dll (64-bit
# applications) and C:\Windows\SysWOW64\weasel.dll (32-bit ones); the copies
# in C:\Program Files\Rime\weasel-<version>\ are only what the installer
# copies from. (The first version of install_tsf.ps1 replaced those, so the
# patch was never loaded.)
#
# Run elevated, from 64-bit PowerShell. In-use DLLs cannot be overwritten but
# can be renamed: the originals become weasel.dll.orig-<stamp> next to the
# new files, and running applications keep the old DLL until they restart.
# Undo: uninstall_tsf_system.ps1.
param(
  [string]$Build = 'D:\src\weasel\output',
  [string]$Log = 'D:\src\weasel\install_tsf_system.log'
)
$ErrorActionPreference = 'Stop'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
"install $stamp" | Out-File $Log -Encoding utf8
try {
  if (-not [Environment]::Is64BitProcess) { throw 'run this from 64-bit PowerShell (System32 is redirected otherwise)' }
  $map = @(
    @{ New = Join-Path $Build 'weaselx64.dll'; Target = "$env:windir\System32\weasel.dll" },
    @{ New = Join-Path $Build 'weasel.dll';    Target = "$env:windir\SysWOW64\weasel.dll" }
  )
  foreach ($m in $map) { if (-not (Test-Path $m.New)) { throw "missing $($m.New)" } }
  foreach ($m in $map) {
    $backup = "$($m.Target).orig-$stamp"
    Rename-Item $m.Target $backup
    try {
      Copy-Item $m.New $m.Target
    } catch {
      Rename-Item $backup $m.Target  # put the original back
      throw
    }
    "$($m.Target) <- $($m.New) (original kept as $backup)" | Out-File $Log -Append -Encoding utf8
  }
  'done' | Out-File $Log -Append -Encoding utf8
} catch {
  "FAILED: $($_.Exception.Message)" | Out-File $Log -Append -Encoding utf8
  exit 1
}

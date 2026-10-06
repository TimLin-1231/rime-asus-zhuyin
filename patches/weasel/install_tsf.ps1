# Install the patched Weasel TSF DLLs (keep unfinished compositions).
# Run elevated. In-use DLLs cannot be overwritten but can be renamed, so the
# originals are renamed to *.orig-<stamp> and the new files copied in; running
# apps keep the old DLL until they restart.
# Undo: tools in D:\src\weasel\uninstall_tsf.ps1 (copies the backups back).
$ErrorActionPreference = 'Stop'
$dir = 'C:\Program Files\Rime\weasel-0.17.4'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$new = @{
  'weasel.dll'    = 'D:\src\weasel\output\weasel.dll'
  'weaselx64.dll' = 'D:\src\weasel\output\weaselx64.dll'
}
$log = 'D:\src\weasel\install_tsf.log'
"install $stamp" | Out-File $log -Encoding utf8
foreach ($name in $new.Keys) {
  $target = Join-Path $dir $name
  Rename-Item $target "$name.orig-$stamp"
  Copy-Item $new[$name] $target
  "$name -> replaced (original kept as $name.orig-$stamp)" | Out-File $log -Append -Encoding utf8
}
"done" | Out-File $log -Append -Encoding utf8

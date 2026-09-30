$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut('C:\Users\hongw\Desktop\BassStation.lnk')
$Shortcut.TargetPath = 'E:\BassStation\release\BassStation.exe'
$Shortcut.WorkingDirectory = 'E:\BassStation\release'
$Shortcut.IconLocation = 'E:\BassStation\release\BassStation.ico,0'
$Shortcut.Description = 'BassStation 2.0 - J-Rock & Low-End Stage'
$Shortcut.Save()

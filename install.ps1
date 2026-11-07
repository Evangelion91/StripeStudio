param(
    [string]$InstallDir = "$env:LOCALAPPDATA\Programs\StripeStudio",
    [switch]$NoPath,
    [switch]$NoShortcuts
)
$ErrorActionPreference = 'Stop'
$sourceDir = $PSScriptRoot
$targetDir = [IO.Path]::GetFullPath($InstallDir)
foreach ($toolName in @('ffmpeg', 'ffprobe')) {
    if (-not (Get-Command $toolName -ErrorAction SilentlyContinue)) { throw "$toolName is required in PATH. Install FFmpeg first." }
}
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) { throw 'Python 3.10+ is required in PATH.' }
$pythonExe = & $pythonCmd.Source -c "import sys; assert sys.version_info >= (3,10), 'Python 3.10+ required'; print(sys.executable)"
if ($LASTEXITCODE -ne 0) { throw 'Python check failed.' }
New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
if ($targetDir.TrimEnd('\') -ne $sourceDir.TrimEnd('\')) {
    foreach ($fileName in @('app.py','mask.py','requirements.txt','reference.preset.json','README.md','AGENTS.md')) {
        Copy-Item -LiteralPath (Join-Path $sourceDir $fileName) -Destination (Join-Path $targetDir $fileName) -Force
    }
    foreach ($folderName in @('web','assets')) {
        $destFolder = Join-Path $targetDir $folderName
        New-Item -ItemType Directory -Force -Path $destFolder | Out-Null
        Get-ChildItem -LiteralPath (Join-Path $sourceDir $folderName) | Copy-Item -Destination $destFolder -Recurse -Force
    }
}
& $pythonExe -m venv (Join-Path $targetDir '.venv')
if ($LASTEXITCODE -ne 0) { throw 'Creating Python environment failed.' }
$venvPython = Join-Path $targetDir '.venv\Scripts\python.exe'
& $venvPython -m pip install -r (Join-Path $targetDir 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Installing Python dependencies failed.' }
$launcher = Join-Path $targetDir 'StripeStudio.cmd'
$launcherText = "@echo off`r`n`"$venvPython`" `"$targetDir\app.py`" %*`r`nexit /b %errorlevel%`r`n"
[IO.File]::WriteAllText($launcher, $launcherText, [Text.Encoding]::Default)
if (-not $NoPath) {
    $envKey = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Environment')
    try {
        $rawPath = [string]$envKey.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
        $valueKind = if ($envKey.GetValueNames() -contains 'Path') { $envKey.GetValueKind('Path') } else { [Microsoft.Win32.RegistryValueKind]::ExpandString }
        if (-not (($rawPath -split ';' | ForEach-Object { [Environment]::ExpandEnvironmentVariables($_).TrimEnd('\') }) -contains $targetDir.TrimEnd('\'))) {
            $envKey.SetValue('Path', ($rawPath.TrimEnd(';') + ';' + $targetDir).TrimStart(';'), $valueKind)
        }
    } finally { $envKey.Dispose() }
    Add-Type -Namespace StripeStudio -Name EnvironmentNotify -MemberDefinition '[System.Runtime.InteropServices.DllImport("user32.dll", CharSet=System.Runtime.InteropServices.CharSet.Unicode)] public static extern System.IntPtr SendMessageTimeout(System.IntPtr h, uint m, System.UIntPtr w, string l, uint flags, uint timeout, out System.UIntPtr result);'
    $notifyResult = [UIntPtr]::Zero
    [void][StripeStudio.EnvironmentNotify]::SendMessageTimeout([IntPtr]0xffff, 0x1a, [UIntPtr]::Zero, 'Environment', 2, 3000, [ref]$notifyResult)
}
if (-not $NoShortcuts) {
    $shell = New-Object -ComObject WScript.Shell
    foreach ($shortcutFolder in @([Environment]::GetFolderPath('Desktop'), (Join-Path ([Environment]::GetFolderPath('Programs')) 'StripeStudio'))) {
        New-Item -ItemType Directory -Force -Path $shortcutFolder | Out-Null
        $shortcut = $shell.CreateShortcut((Join-Path $shortcutFolder 'StripeStudio.lnk'))
        $shortcut.TargetPath = $venvPython
        $shortcut.Arguments = '"' + (Join-Path $targetDir 'app.py') + '" serve'
        $shortcut.WorkingDirectory = $targetDir
        $shortcut.Save()
    }
}
& $venvPython (Join-Path $targetDir 'app.py') --version
if ($LASTEXITCODE -ne 0) { throw 'Installation verification failed.' }
Write-Host "Installed: $targetDir. Open a new terminal to use StripeStudio."

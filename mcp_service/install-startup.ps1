param(
    [Parameter(Mandatory = $true)]
    [string]$PythonPath
)

$ErrorActionPreference = 'Stop'
$arkPython = (Resolve-Path -LiteralPath $PythonPath).Path
if ([IO.Path]::GetFileName($arkPython) -ne 'pythonw.exe') {
    throw 'Use pythonw.exe from the Python installation used by the MCP gateway.'
}
$arkLaunch = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'launch.py')).Path
$arkPackage = Split-Path -Parent $PSScriptRoot
$arkTaskName = 'Arkennemasis MCP'
$arkArguments = '"' + $arkLaunch + '" web'
$arkExisting = Get-ScheduledTask -TaskName $arkTaskName -ErrorAction SilentlyContinue
if ($arkExisting) {
    if ($arkExisting.Actions.Count -ne 1 -or $arkExisting.Actions[0].Arguments -ne $arkArguments) {
        throw 'A different task already uses the name Arkennemasis MCP. It was not changed.'
    }
}
$arkUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$arkAction = New-ScheduledTaskAction -Execute $arkPython -Argument $arkArguments -WorkingDirectory $arkPackage
$arkTrigger = New-ScheduledTaskTrigger -AtLogOn -User $arkUser
$arkPrincipal = New-ScheduledTaskPrincipal -UserId $arkUser -LogonType Interactive -RunLevel Limited
$arkSettings = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $arkTaskName -Action $arkAction -Trigger $arkTrigger `
    -Principal $arkPrincipal -Settings $arkSettings `
    -Description 'Start the Arkennemasis MCP gateway at sign-in using its saved fixed HTTPS address.' `
    -Force | Select-Object TaskName, State

param([Parameter(ValueFromRemainingArguments = $true)]$Args)
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
& (Join-Path $ScriptDir 'build_orion_desktop.ps1') @Args
exit $LASTEXITCODE

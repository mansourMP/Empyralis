param([Parameter(ValueFromRemainingArguments = $true)]$Args)
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
& (Join-Path $ScriptDir 'stop_orion_local_stack.ps1') @Args
exit $LASTEXITCODE

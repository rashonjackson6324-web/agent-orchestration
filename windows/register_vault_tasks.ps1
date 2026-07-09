# Register vault_manager and auto_organizer as scheduled tasks

$vaultScript = "<LOCAL_PATH>"
$coworkScript = "<LOCAL_PATH>"
$python = "<LOCAL_PATH>"

# Vault Manager - every 30 minutes
$action1 = New-ScheduledTaskAction -Execute $python -Argument $vaultScript
$trigger1 = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 30) -Once -At (Get-Date)
Register-ScheduledTask -TaskName "the agent network-VaultManager" -Action $action1 -Trigger $trigger1 -RunLevel Highest -Force

# Cowork Agent (AnythingLLM sync) - every 60 minutes
$action2 = New-ScheduledTaskAction -Execute $python -Argument $coworkScript
$trigger2 = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 60) -Once -At (Get-Date)
Register-ScheduledTask -TaskName "the agent network-CoworkSync" -Action $action2 -Trigger $trigger2 -RunLevel Highest -Force

Write-Host "Tasks registered." -ForegroundColor Green
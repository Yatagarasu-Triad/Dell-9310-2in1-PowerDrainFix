#requires -Version 5.1
<# Read-only Windows lid comparison. See WINDOWS_TEST.md before running.
   Writes only a private results directory; does not request or inhibit sleep.
#>
[CmdletBinding()]
param([string]$OutputRoot)

$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'Windows is required. No capture was started.'
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run once from an Administrator Windows PowerShell window. No self-elevation is performed.'
}

if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $local = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    if ([string]::IsNullOrWhiteSpace($local)) { throw 'LocalApplicationData is unavailable; supply -OutputRoot.' }
    $OutputRoot = Join-Path $local 'Dell9310-LidCapture'
}
if ($OutputRoot -notmatch '^[A-Za-z]:[\\/]') { throw 'OutputRoot must be an absolute local drive path, not a UNC path.' }
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
$drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($OutputRoot))
if ($drive.DriveType -eq [IO.DriveType]::Network) { throw 'Use local storage, not a mapped network drive.' }
$null = New-Item -ItemType Directory -Path $OutputRoot -Force
$run = Join-Path $OutputRoot ('lid-' + [datetime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $run
# Protect only our new directory, before writing evidence; never relax parent ACLs.
$acl = [Security.AccessControl.DirectorySecurity]::new()
$acl.SetAccessRuleProtection($true, $false)
$acl.SetOwner($identity.User)
foreach ($sid in @($identity.User, [Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
        $sid, 'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow'))
}
Set-Acl -LiteralPath $run -AclObject $acl

$powercfg = Join-Path ([Environment]::SystemDirectory) 'powercfg.exe'
$windowsPowerShell = Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe'
$commands = [Collections.Generic.List[object]]::new()
$manifest = [ordered]@{
    FormatVersion = 1
    State = 'preparing'
    StartedUtc = [datetime]::UtcNow.ToString('o')
    HostPowerShellVersion = $PSVersionTable.PSVersion.ToString()
    ResultDirectory = $run
    ReadyUtc = $null
    ObservationStartUtc = $null
    ObservationDeadlineUtc = $null
    ObservationEndUtc = $null
    CollectionDeadlineUtc = $null
    FinishedUtc = $null
    EventIntervalStartUtc = $null
    EventIntervalEndUtc = $null
    ObservationSeconds = 90
    EventMarginSeconds = 15
    Confounders = [ordered]@{ AcChange = 'unknown'; PolicyQueryChange = 'unknown'; LidPolicyQueryChange = 'unknown' }
    Interpretation = 'Unclassified: correlate policy, physical closure and event reasons; missing events are not a lid verdict.'
    Privacy = 'Private local evidence, not anonymized. Reports/events may include identifiers, paths and earlier activity. Review before sharing.'
    Error = $null
}
function Save-Json($Value, [string]$Name) {
    ConvertTo-Json -InputObject $Value -Depth 8 | Set-Content -LiteralPath (Join-Path $run $Name) -Encoding UTF8
}
function Wait-Until([datetime]$Deadline) {
    # UTC deadlines include time asleep; no wake timer or RTC writes are used.
    while ([datetime]::UtcNow -lt $Deadline) { Start-Sleep -Milliseconds 200 }
}
function Encode-Command([string]$Code) {
    $text = '[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); ' + $Code
    return '-NoLogo -NoProfile -NonInteractive -EncodedCommand ' + [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($text))
}
function Capture-Command([string]$Name, [string]$Executable, [string]$Arguments,
                         [int]$Seconds, [datetime]$PhaseDeadline, [string]$ReportFile = '') {
    $started = [datetime]::UtcNow
    $deadline = $started.AddSeconds($Seconds)
    if ($deadline -gt $PhaseDeadline) { $deadline = $PhaseDeadline }
    $record = [ordered]@{
        Name = $Name; StartedUtc = $started.ToString('o'); DeadlineUtc = $deadline.ToString('o')
        EndedUtc = $null; Status = 'skipped_deadline'; ExitCode = $null; Error = $null
        Stdout = "$Name.stdout.txt"; Stderr = "$Name.stderr.txt"
        ReportFile = $ReportFile; ReportState = 'not_requested'
    }
    $process = $null
    try {
        if ($started -lt $deadline) {
            $process = Start-Process -FilePath $Executable -ArgumentList $Arguments -WorkingDirectory $run -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $run $record.Stdout) -RedirectStandardError (Join-Path $run $record.Stderr)
            $null = $process.Handle
            while (-not $process.HasExited -and [datetime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 200 }
            if ($process.HasExited) {
                $record.ExitCode = $process.ExitCode
                if ($null -eq $record.ExitCode) { $record.Status = 'unknown_exit' }
                elseif ($record.ExitCode -eq 0) { $record.Status = 'ok' }
                else { $record.Status = 'nonzero_exit'; $record.Error = 'See stdout/stderr; command may be unsupported or have failed.' }
            } else {
                $record.Status = 'timeout'
                $process.Kill()
                if (-not $process.WaitForExit(1000)) { $record.Error = 'Termination requested, but not confirmed within one second.' }
            }
        }
    } catch {
        $record.Error = $_.Exception.Message
        if ($record.Status -ne 'timeout') { $record.Status = 'error' }
        if ($null -ne $process) {
            try { if (-not $process.HasExited) { $process.Kill() } } catch { $record.Error += '; termination: ' + $_.Exception.Message }
        }
    } finally {
        if ($null -ne $process) { $process.Dispose() }
        if ($ReportFile) {
            $reportPath = Join-Path $run $ReportFile
            if (-not (Test-Path -LiteralPath $reportPath)) { $record.ReportState = 'missing' }
            elseif ((Get-Item -LiteralPath $reportPath).Length -eq 0) { $record.ReportState = 'empty' }
            elseif ($record.Status -eq 'ok') { $record.ReportState = 'present' }
            else { $record.ReportState = 'partial_or_unverified' }
            if ($record.Status -eq 'ok' -and $record.ReportState -ne 'present') { $record.Status = 'report_missing_or_empty' }
        }
        $record.EndedUtc = [datetime]::UtcNow.ToString('o')
        $commands.Add($record)
        Save-Json -Value @($commands.ToArray()) -Name 'commands.json'
    }
    if ($record.Status -ne 'ok') { Write-Warning "$Name : $($record.Status). Other evidence will still be collected." }
}
function Capture-SleepDiagnostics([datetime]$PhaseDeadline) {
    Capture-Command 'systemsleepdiagnostics' $powercfg ('/systemsleepdiagnostics /output "' + (Join-Path $run 'system-sleep-diagnostics.html') + '"') 45 $PhaseDeadline 'system-sleep-diagnostics.html'
    $legacyReport = $commands[$commands.Count - 1]
    if ($legacyReport.Status -ne 'ok' -or $legacyReport.ReportState -ne 'present') {
        Capture-Command 'systempowerreport' $powercfg ('/systempowerreport /output "' + (Join-Path $run 'system-power-report.html') + '"') 45 $PhaseDeadline 'system-power-report.html'
    }
}

# Small child captures keep CIM/event queries bounded too. Only built-in commands
# run there; no profile, script-policy override, elevation or installed dependency.
$snapshotCode = {
    $ErrorActionPreference = 'Stop'
    $data = [ordered]@{ StartedUtc = [datetime]::UtcNow.ToString('o'); OS = $null; BIOS = $null; Power = $null; Errors = @() }
    try {
        $os = Get-CimInstance -ClassName Win32_OperatingSystem -Property Caption, Version, BuildNumber, LastBootUpTime -OperationTimeoutSec 8
        $data.OS = [ordered]@{ Caption = $os.Caption; Version = $os.Version; BuildNumber = $os.BuildNumber; LastBootUpTimeUtc = $os.LastBootUpTime.ToUniversalTime().ToString('o') }
    } catch { $data.Errors += 'OS: ' + $_.Exception.Message }
    try {
        $bios = Get-CimInstance -ClassName Win32_BIOS -Property SMBIOSBIOSVersion, ReleaseDate -OperationTimeoutSec 8
        $data.BIOS = $bios | Select-Object SMBIOSBIOSVersion, ReleaseDate
    } catch { $data.Errors += 'BIOS: ' + $_.Exception.Message }
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $power = [System.Windows.Forms.SystemInformation]::PowerStatus
        $data.Power = [ordered]@{
            PowerLineStatus = $power.PowerLineStatus.ToString()
            BatteryChargeStatus = $power.BatteryChargeStatus.ToString()
            BatteryLifeFraction = $power.BatteryLifePercent
            BatteryLifeRemainingSeconds = $power.BatteryLifeRemaining
        }
    } catch { $data.Errors += 'AC/battery: ' + $_.Exception.Message }
    $data.FinishedUtc = [datetime]::UtcNow.ToString('o')
    [Console]::WriteLine(($data | ConvertTo-Json -Depth 5))
}
$eventsCode = {
    param([datetime]$FromUtc, [datetime]$ToUtc)
    $ErrorActionPreference = 'Stop'
    $summary = [ordered]@{ Status = 'captured'; Count = 0; Error = $null }
    $xml = [IO.StreamWriter]::new('events.xml', $false, [Text.UTF8Encoding]::new($false))
    $messages = [IO.StreamWriter]::new('event-messages.txt', $false, [Text.UTF8Encoding]::new($false))
    $xml.AutoFlush = $true
    $messages.AutoFlush = $true
    $xml.WriteLine('<Events>')
    try {
        Get-WinEvent -FilterHashtable @{
            LogName = 'System'
            ProviderName = @('Microsoft-Windows-Kernel-Power', 'Microsoft-Windows-Power-Troubleshooter')
            StartTime = $FromUtc.ToLocalTime(); EndTime = $ToUtc.ToLocalTime()
        } -Oldest -ErrorAction Stop | ForEach-Object {
            $xml.WriteLine($_.ToXml())
            $messages.WriteLine(('{0} | {1} | ID {2} | Record {3}' -f $_.TimeCreated.ToUniversalTime().ToString('o'), $_.ProviderName, $_.Id, $_.RecordId))
            try { $messages.WriteLine($_.Message) } catch { $messages.WriteLine('Message unavailable: ' + $_.Exception.Message) }
            $messages.WriteLine()
            $summary.Count++
        }
    } catch {
        if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { $summary.Status = 'no_matching_events' }
        else { $summary.Status = 'query_error' }
        $summary.Error = $_.Exception.Message
    } finally {
        $xml.WriteLine('</Events>'); $xml.Dispose(); $messages.Dispose()
    }
    [Console]::WriteLine(($summary | ConvertTo-Json))
    if ($summary.Status -eq 'query_error') { exit 1 }
}

try {
    Save-Json $manifest 'manifest.json'
    Write-Host "Preparing read-only capture. Keep lid OPEN. Private results: $run"
    $setupDeadline = [datetime]::UtcNow.AddSeconds(75)
    $snapshotArguments = Encode-Command $snapshotCode.ToString()
    Capture-Command 'before-context' $windowsPowerShell $snapshotArguments 30 $setupDeadline
    Capture-Command 'before-sleep-states' $powercfg '/a' 15 $setupDeadline
    Capture-Command 'before-policy' $powercfg '/query' 15 $setupDeadline
    Capture-Command 'before-lid-policy' $powercfg '/qh SCHEME_CURRENT SUB_BUTTONS' 15 $setupDeadline

    $start = [datetime]::UtcNow
    $deadline = $start.AddSeconds(90)
    $collectionDeadline = $deadline.AddSeconds(180)
    $manifest.State = 'observing'
    $manifest.ReadyUtc = $start.ToString('o')
    $manifest.ObservationStartUtc = $start.ToString('o')
    $manifest.ObservationDeadlineUtc = $deadline.ToString('o')
    $manifest.CollectionDeadlineUtc = $collectionDeadline.ToString('o')
    Save-Json $manifest 'manifest.json'
    Write-Host "Lid recording ready / START ($($start.ToString('o'))). Close normally for 10 seconds, then reopen promptly."
    Write-Host 'Leave AC/devices/policy unchanged. Wait for Lid capture ENDED before confirming. No further input is required.'
    Wait-Until $deadline
    $end = [datetime]::UtcNow
    $manifest.ObservationEndUtc = $end.ToString('o')
    $manifest.State = 'collecting'
    $from = $start.AddSeconds(-15)
    $to = $end.AddSeconds(15)
    $manifest.EventIntervalStartUtc = $from.ToString('o')
    $manifest.EventIntervalEndUtc = $to.ToString('o')
    Save-Json $manifest 'manifest.json'
    Write-Host 'Observation window closed. Keep lid open; finishing bounded reports (up to about 3 minutes).'
    Capture-Command 'after-context' $windowsPowerShell $snapshotArguments 30 $collectionDeadline
    Capture-Command 'after-sleep-states' $powercfg '/a' 15 $collectionDeadline
    Capture-Command 'after-policy' $powercfg '/query' 15 $collectionDeadline
    Capture-Command 'after-lid-policy' $powercfg '/qh SCHEME_CURRENT SUB_BUTTONS' 15 $collectionDeadline
    if ($to -lt $collectionDeadline) { Wait-Until $to }
    $eventInvocation = '& {' + $eventsCode.ToString() + "} '$($from.ToString('o'))' '$($to.ToString('o'))'"
    Capture-Command 'events' $windowsPowerShell (Encode-Command $eventInvocation) 20 $collectionDeadline
    Capture-SleepDiagnostics $collectionDeadline
    Capture-Command 'sleepstudy' $powercfg ('/sleepstudy /output "' + (Join-Path $run 'sleepstudy.html') + '"') 45 $collectionDeadline 'sleepstudy.html'

    # Endpoint checks flag confounders, not sensor outcomes; transient changes can be missed.
    try {
        $before = Get-Content -LiteralPath (Join-Path $run 'before-context.stdout.txt') -Raw -Encoding UTF8 | ConvertFrom-Json
        $after = Get-Content -LiteralPath (Join-Path $run 'after-context.stdout.txt') -Raw -Encoding UTF8 | ConvertFrom-Json
        $acBefore = $before.Power.PowerLineStatus
        $acAfter = $after.Power.PowerLineStatus
        if ($acBefore -in @('Online', 'Offline') -and $acAfter -in @('Online', 'Offline')) {
            if ($acBefore -ne $acAfter) { $manifest.Confounders.AcChange = 'changed' }
            else { $manifest.Confounders.AcChange = 'same_at_endpoints_only' }
        }
    } catch { $manifest.Confounders.AcChange = 'unknown: ' + $_.Exception.Message }
    if (@($commands | Where-Object { $_.Name -in @('before-policy', 'after-policy') -and $_.Status -eq 'ok' }).Count -eq 2) {
        $beforeHash = (Get-FileHash -LiteralPath (Join-Path $run 'before-policy.stdout.txt') -Algorithm SHA256).Hash
        $afterHash = (Get-FileHash -LiteralPath (Join-Path $run 'after-policy.stdout.txt') -Algorithm SHA256).Hash
        if ($beforeHash -ne $afterHash) { $manifest.Confounders.PolicyQueryChange = 'changed_query_output_review_as_confounder' }
        else { $manifest.Confounders.PolicyQueryChange = 'same_query_output_at_endpoints_only' }
    }
    if (@($commands | Where-Object { $_.Name -in @('before-lid-policy', 'after-lid-policy') -and $_.Status -eq 'ok' }).Count -eq 2) {
        $beforeLidHash = (Get-FileHash -LiteralPath (Join-Path $run 'before-lid-policy.stdout.txt') -Algorithm SHA256).Hash
        $afterLidHash = (Get-FileHash -LiteralPath (Join-Path $run 'after-lid-policy.stdout.txt') -Algorithm SHA256).Hash
        if ($beforeLidHash -ne $afterLidHash) { $manifest.Confounders.LidPolicyQueryChange = 'changed_query_output_review_as_confounder' }
        else { $manifest.Confounders.LidPolicyQueryChange = 'same_query_output_at_endpoints_only' }
    }
    $manifest.State = 'collection_finished_review_command_status_and_snapshot_errors'
} catch {
    $manifest.State = 'incomplete'
    $manifest.Error = $_.Exception.Message
    Write-Warning "Capture interrupted by error: $($manifest.Error)"
} finally {
    $manifest.FinishedUtc = [datetime]::UtcNow.ToString('o')
    Save-Json $manifest 'manifest.json'
    Write-Host "Lid capture ENDED ($($manifest.FinishedUtc)). $($manifest.State)"
    Write-Host "Results: $run"
    Write-Host 'No automatic lid success/failure verdict. Review manifest.json, commands.json, policy and event reasons. You may now confirm completion.'
}

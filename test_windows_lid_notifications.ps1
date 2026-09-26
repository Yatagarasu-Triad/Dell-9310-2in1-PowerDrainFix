#requires -Version 5.1
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$parseTokens = $null
$parseErrors = $null
$sourcePath = Join-Path $PSScriptRoot 'windows_lid_notifications.ps1'
$sourceAst = [System.Management.Automation.Language.Parser]::ParseFile($sourcePath, [ref]$parseTokens, [ref]$parseErrors)
if (@($parseErrors).Count -ne 0) { throw ($parseErrors | Out-String) }
$sourceAssignment = $sourceAst.Find({
    param($node)
    $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and $node.Left.Extent.Text -eq '$nativeCode'
}, $true)
if ($null -eq $sourceAssignment) { throw 'Embedded native helper source was not found.' }
$nativeSource = $sourceAssignment.Right.Expression.Value
if ([string]::IsNullOrWhiteSpace($nativeSource)) { throw 'Embedded native helper source is empty.' }

$testHelper = @'
public static class LidNotificationSyntheticChecks {
    public static void CallbackFromWorker(Dell9310.LidNotificationListener listener, System.IntPtr payload) {
        System.Exception failure = null;
        System.Threading.Thread worker = new System.Threading.Thread(delegate() {
            try {
                System.Reflection.FieldInfo field = typeof(Dell9310.LidNotificationListener).GetField("callback", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic);
                System.Delegate callback = (System.Delegate)field.GetValue(listener);
                object result = callback.DynamicInvoke(System.IntPtr.Zero, (uint)0x8013, payload);
                if ((uint)result != 0) { throw new System.InvalidOperationException("Callback returned an error."); }
            } catch (System.Exception error) { failure = error; }
        });
        worker.IsBackground = true;
        worker.Start();
        if (!worker.Join(5000)) { throw new System.TimeoutException("Synthetic callback did not finish."); }
        if (failure != null) { throw failure; }
    }
}
'@
if ('Dell9310.LidNotificationListener' -as [type]) { throw 'Run tests in a fresh Windows PowerShell process so current source is compiled.' }
Add-Type -TypeDefinition ($nativeSource + [Environment]::NewLine + $testHelper)

if ([Dell9310.LidNotificationListener]::GuidOffset -ne 0 -or
    [Dell9310.LidNotificationListener]::LengthOffset -ne 16 -or
    [Dell9310.LidNotificationListener]::DataOffset -ne 20 -or
    [Runtime.InteropServices.Marshal]::SizeOf((New-Object Dell9310.PowerSettingPayload)) -ne 24) {
    throw 'POWERBROADCAST_SETTING DWORD layout does not match the Windows SDK.'
}
$subscriptionType = [Dell9310.LidNotificationListener].Assembly.GetType('Dell9310.SubscribeParameters')
if ([Runtime.InteropServices.Marshal]::SizeOf([Activator]::CreateInstance($subscriptionType)) -ne 2 * [IntPtr]::Size) {
    throw 'Callback subscription layout must contain two pointer-sized fields.'
}

$payload = [Runtime.InteropServices.Marshal]::AllocHGlobal(24)
$cases = @(
    @{ Guid = [Dell9310.LidNotificationListener]::LidGuid; Length = 4; Value = 0; Status = 'state'; State = 'closed'; Setting = 'lid' },
    @{ Guid = [Dell9310.LidNotificationListener]::LidGuid; Length = 4; Value = 1; Status = 'state'; State = 'open'; Setting = 'lid' },
    @{ Guid = [Dell9310.LidNotificationListener]::LidGuid; Length = 4; Value = 2; Status = 'unknown_value'; State = 'unknown'; Setting = 'lid' },
    @{ Guid = [Dell9310.LidNotificationListener]::LidGuid; Length = 0; Value = 1; Status = 'invalid_length'; State = 'unknown'; Setting = 'lid' },
    @{ Guid = [Dell9310.LidNotificationListener]::LidGuid; Length = 3; Value = 1; Status = 'invalid_length'; State = 'unknown'; Setting = 'lid' },
    @{ Guid = [Dell9310.LidNotificationListener]::LidGuid; Length = 8; Value = 1; Status = 'invalid_length'; State = 'unknown'; Setting = 'lid' },
    @{ Guid = [guid]::Empty; Length = 4; Value = 1; Status = 'ignored_guid'; State = 'unknown'; Setting = $null },
    @{ Guid = [Dell9310.LidNotificationListener]::AcDcGuid; Length = 4; Value = 0; Status = 'state'; State = 'ac'; Setting = 'acdc' },
    @{ Guid = [Dell9310.LidNotificationListener]::AcDcGuid; Length = 4; Value = 1; Status = 'state'; State = 'battery'; Setting = 'acdc' },
    @{ Guid = [Dell9310.LidNotificationListener]::AcDcGuid; Length = 4; Value = 2; Status = 'state'; State = 'short_term_source'; Setting = 'acdc' },
    @{ Guid = [Dell9310.LidNotificationListener]::AcDcGuid; Length = 4; Value = 3; Status = 'unknown_value'; State = 'unknown'; Setting = 'acdc' }
)
try {
    foreach ($testCase in $cases) {
        $structure = New-Object Dell9310.PowerSettingPayload
        $structure.PowerSetting = $testCase.Guid
        $structure.DataLength = $testCase.Length
        $structure.Data = $testCase.Value
        [Runtime.InteropServices.Marshal]::StructureToPtr($structure, $payload, $false)
        $decoded = [Dell9310.LidNotificationListener]::Decode(0x8013, $payload)
        foreach ($property in @('Status', 'State', 'Setting')) {
            if ($decoded.$property -ne $testCase[$property]) { throw "Decoder $property mismatch for $($testCase.Status) / $($testCase.Value)." }
        }
        if ($decoded.Status -in @('invalid_length', 'ignored_guid') -and $null -ne $decoded.Value) { throw 'Decoder read an unsupported payload value.' }
    }
    $nullPayload = [Dell9310.LidNotificationListener]::Decode(0x8013, [IntPtr]::Zero)
    if ($nullPayload.Status -ne 'null_payload' -or $nullPayload.State -ne 'unknown') { throw 'Null payload was interpreted as lid state.' }
    $wrongType = [Dell9310.LidNotificationListener]::Decode(0, [IntPtr]::new(1))
    if ($wrongType.Status -ne 'ignored_type' -or $wrongType.State -ne 'unknown') { throw 'Wrong message type was not rejected before dereferencing.' }

    $listener = [Dell9310.LidNotificationListener]::new()
    if (@($listener.Registrations).Count -ne 0 -or @($listener.Drain()).Count -ne 0) { throw 'Constructing the listener unexpectedly registered or produced state.' }
    $structure.PowerSetting = [Dell9310.LidNotificationListener]::LidGuid
    $structure.DataLength = 4
    $structure.Data = 1
    [Runtime.InteropServices.Marshal]::StructureToPtr($structure, $payload, $false)
    $callbackField = [Dell9310.LidNotificationListener].GetField('callback', [Reflection.BindingFlags]'Instance, NonPublic')
    $weakCallback = [WeakReference]::new($callbackField.GetValue($listener))
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
    if (-not $weakCallback.IsAlive) { throw 'The native callback delegate lost its managed root.' }
    [LidNotificationSyntheticChecks]::CallbackFromWorker($listener, $payload)
    $observations = @($listener.Drain())
    if ($observations.Count -ne 1 -or $observations[0].State -ne 'open') { throw 'Worker-thread callback failed to queue the decoded notification.' }
    if (@($listener.Drain()).Count -ne 0) { throw 'Draining replayed a notification.' }
    $listener.Stop()
    $listener.Stop()
    if (@($listener.Registrations).Count -ne 0) { throw 'Synthetic tests performed a real registration.' }
} finally {
    [Runtime.InteropServices.Marshal]::FreeHGlobal($payload)
}

Write-Host "PASS: PowerShell parser, C# compilation, native layouts, $($cases.Count + 2) decoder cases, callback lifetime/worker queue, and idempotent unregistered cleanup. No hardware registration or physical test ran."

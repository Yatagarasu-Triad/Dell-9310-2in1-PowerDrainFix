#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateRange(5, 300)][int]$Seconds = 90,
    [switch]$BaselineOnly,
    [string]$OutputRoot
)

$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'Windows is required. No listener was started.'
}

$nativeCode = @'
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Runtime.InteropServices;

namespace Dell9310 {
    [StructLayout(LayoutKind.Sequential)]
    public struct PowerSettingPayload {
        public Guid PowerSetting;
        public uint DataLength;
        public uint Data;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct SubscribeParameters {
        internal IntPtr Callback;
        internal IntPtr Context;
    }

    public sealed class NotificationRecord {
        public string TimestampUtc { get; set; }
        public string Kind { get; set; }
        public uint Type { get; set; }
        public string Setting { get; set; }
        public string Guid { get; set; }
        public uint? DataLength { get; set; }
        public uint? Value { get; set; }
        public string State { get; set; }
        public string Status { get; set; }
        public string Error { get; set; }
    }

    public sealed class RegistrationRecord {
        public string Setting { get; set; }
        public string Guid { get; set; }
        public string StartedUtc { get; set; }
        public string CompletedUtc { get; set; }
        public uint? ResultCode { get; set; }
        public bool Registered { get; set; }
        public string Error { get; set; }
        public string UnregisterStartedUtc { get; set; }
        public string UnregisterCompletedUtc { get; set; }
        public uint? UnregisterResultCode { get; set; }
        public string UnregisterError { get; set; }
        internal IntPtr Handle;
    }

    public sealed class LidNotificationListener {
        public static readonly Guid LidGuid = new Guid("BA3E0F4D-B817-4094-A2D1-D56379E6A0F3");
        public static readonly Guid AcDcGuid = new Guid("5D3E9A59-E9D5-4B00-A6BD-FF34FF516548");
        public static readonly int GuidOffset = Marshal.OffsetOf(typeof(PowerSettingPayload), "PowerSetting").ToInt32();
        public static readonly int LengthOffset = Marshal.OffsetOf(typeof(PowerSettingPayload), "DataLength").ToInt32();
        public static readonly int DataOffset = Marshal.OffsetOf(typeof(PowerSettingPayload), "Data").ToInt32();
        private static readonly List<LidNotificationListener> Roots = new List<LidNotificationListener>();
        private readonly ConcurrentQueue<NotificationRecord> queue = new ConcurrentQueue<NotificationRecord>();
        private readonly List<RegistrationRecord> registrations = new List<RegistrationRecord>();
        private readonly NativeCallback callback;
        private readonly IntPtr recipient;
        private bool started;
        private bool stopped;

        [UnmanagedFunctionPointer(CallingConvention.Winapi)]
        private delegate uint NativeCallback(IntPtr context, uint type, IntPtr setting);

        [DllImport("powrprof.dll", CallingConvention = CallingConvention.Winapi)]
        private static extern uint PowerSettingRegisterNotification(ref Guid settingGuid, uint flags, IntPtr recipient, out IntPtr registrationHandle);

        [DllImport("powrprof.dll", CallingConvention = CallingConvention.Winapi)]
        private static extern uint PowerSettingUnregisterNotification(IntPtr registrationHandle);

        public LidNotificationListener() {
            callback = Receive;
            SubscribeParameters parameters = new SubscribeParameters();
            parameters.Callback = Marshal.GetFunctionPointerForDelegate(callback);
            parameters.Context = IntPtr.Zero;
            recipient = Marshal.AllocHGlobal(Marshal.SizeOf(typeof(SubscribeParameters)));
            Marshal.StructureToPtr(parameters, recipient, false);
            lock (Roots) { Roots.Add(this); }
        }

        public RegistrationRecord[] Registrations {
            get { return registrations.ToArray(); }
        }

        public static NotificationRecord Decode(uint type, IntPtr setting) {
            NotificationRecord record = new NotificationRecord();
            record.TimestampUtc = DateTime.UtcNow.ToString("o");
            record.Kind = "power_setting";
            record.Type = type;
            record.State = "unknown";
            if (type != 0x8013) { record.Status = "ignored_type"; return record; }
            if (setting == IntPtr.Zero) { record.Status = "null_payload"; return record; }
            Guid settingGuid = (Guid)Marshal.PtrToStructure(IntPtr.Add(setting, GuidOffset), typeof(Guid));
            record.Guid = settingGuid.ToString();
            if (settingGuid == LidGuid) { record.Setting = "lid"; }
            else if (settingGuid == AcDcGuid) { record.Setting = "acdc"; }
            else { record.Status = "ignored_guid"; return record; }
            uint length = unchecked((uint)Marshal.ReadInt32(setting, LengthOffset));
            record.DataLength = length;
            if (length != sizeof(uint)) { record.Status = "invalid_length"; return record; }
            uint value = unchecked((uint)Marshal.ReadInt32(setting, DataOffset));
            record.Value = value;
            record.Status = "state";
            if (settingGuid == LidGuid && value == 0) { record.State = "closed"; }
            else if (settingGuid == LidGuid && value == 1) { record.State = "open"; }
            else if (settingGuid == AcDcGuid && value == 0) { record.State = "ac"; }
            else if (settingGuid == AcDcGuid && value == 1) { record.State = "battery"; }
            else if (settingGuid == AcDcGuid && value == 2) { record.State = "short_term_source"; }
            else { record.Status = "unknown_value"; }
            return record;
        }

        private uint Receive(IntPtr context, uint type, IntPtr setting) {
            try { queue.Enqueue(Decode(type, setting)); }
            catch (Exception error) {
                NotificationRecord record = new NotificationRecord();
                record.TimestampUtc = DateTime.UtcNow.ToString("o");
                record.Kind = "power_setting";
                record.Type = type;
                record.State = "unknown";
                record.Status = "callback_error";
                record.Error = error.Message;
                queue.Enqueue(record);
            }
            return 0;
        }

        private void Register(string name, Guid settingGuid) {
            RegistrationRecord record = new RegistrationRecord();
            record.Setting = name;
            record.Guid = settingGuid.ToString();
            record.StartedUtc = DateTime.UtcNow.ToString("o");
            registrations.Add(record);
            try {
                record.ResultCode = PowerSettingRegisterNotification(ref settingGuid, 2, recipient, out record.Handle);
                record.Registered = record.ResultCode == 0 && record.Handle != IntPtr.Zero;
                if (!record.Registered) { record.Error = "Registration did not return success and a nonzero handle."; }
            } catch (Exception error) { record.Error = error.Message; }
            finally { record.CompletedUtc = DateTime.UtcNow.ToString("o"); }
        }

        public void Start() {
            if (started || stopped) { throw new InvalidOperationException("This listener cannot be restarted."); }
            started = true;
            Register("lid", LidGuid);
            Register("acdc", AcDcGuid);
        }

        public NotificationRecord[] Drain() {
            List<NotificationRecord> records = new List<NotificationRecord>();
            NotificationRecord record;
            while (queue.TryDequeue(out record)) { records.Add(record); }
            return records.ToArray();
        }

        public void Stop() {
            if (stopped) { return; }
            stopped = true;
            foreach (RegistrationRecord record in registrations) {
                if (!record.Registered) { continue; }
                record.UnregisterStartedUtc = DateTime.UtcNow.ToString("o");
                try {
                    record.UnregisterResultCode = PowerSettingUnregisterNotification(record.Handle);
                    if (record.UnregisterResultCode != 0) { record.UnregisterError = "Unregistration failed; callback storage remains rooted until process exit."; }
                } catch (Exception error) { record.UnregisterError = error.Message; }
                finally { record.UnregisterCompletedUtc = DateTime.UtcNow.ToString("o"); }
            }
        }
    }
}
'@

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $local = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    if ([string]::IsNullOrWhiteSpace($local)) { throw 'LocalApplicationData is unavailable; supply -OutputRoot.' }
    $OutputRoot = Join-Path $local 'Dell9310-LidNotifications'
}
if ($OutputRoot -notmatch '^[A-Za-z]:[\\/]') { throw 'OutputRoot must be an absolute local drive path, not a UNC path.' }
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
$drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($OutputRoot))
if ($drive.DriveType -eq [IO.DriveType]::Network) { throw 'Use local storage, not a mapped network drive.' }
$null = New-Item -ItemType Directory -Path $OutputRoot -Force
$run = Join-Path $OutputRoot ('notifications-' + [datetime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $run
$acl = [Security.AccessControl.DirectorySecurity]::new()
$acl.SetAccessRuleProtection($true, $false)
$acl.SetOwner($identity.User)
foreach ($sid in @($identity.User, [Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
        $sid, 'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow'))
}
Set-Acl -LiteralPath $run -AclObject $acl

$manifest = [ordered]@{
    FormatVersion = 1
    State = 'preparing'
    StartedUtc = [datetime]::UtcNow.ToString('o')
    HostPowerShellVersion = $PSVersionTable.PSVersion.ToString()
    ResultDirectory = $run
    BaselineOnly = [bool]$BaselineOnly
    ObservationSeconds = $Seconds
    ReadyUtc = $null
    ObservationDeadlineUtc = $null
    ObservationEndUtc = $null
    FinishedUtc = $null
    Registrations = @()
    Notifications = [ordered]@{
        Total = 0; Lid = 0; AcDc = 0; InvalidOrUnknown = 0
        InitialLidState = 'unknown'; LastLidState = 'unknown'
        InitialAcDcState = 'unknown'; LastAcDcState = 'unknown'
    }
    Interpretation = 'Unclassified: initial callbacks describe current state, not physical transitions. No lid notifications means unknown, not open or closed. Correlate with confirmed physical action; this does not measure sleep or S0ix.'
    Error = $null
}
$listener = $null
$writer = $null
function Save-Manifest {
    ConvertTo-Json -InputObject $manifest -Depth 6 | Set-Content -LiteralPath (Join-Path $run 'manifest.json') -Encoding UTF8
}
function Flush-Notifications {
    foreach ($record in $listener.Drain()) {
        $writer.WriteLine((ConvertTo-Json -InputObject $record -Depth 4 -Compress))
        $manifest.Notifications.Total++
        if ($record.Status -ne 'state') { $manifest.Notifications.InvalidOrUnknown++ }
        if ($record.Setting -eq 'lid') {
            if ($manifest.Notifications.Lid -eq 0) { $manifest.Notifications.InitialLidState = $record.State }
            $manifest.Notifications.Lid++
            $manifest.Notifications.LastLidState = $record.State
        } elseif ($record.Setting -eq 'acdc') {
            if ($manifest.Notifications.AcDc -eq 0) { $manifest.Notifications.InitialAcDcState = $record.State }
            $manifest.Notifications.AcDc++
            $manifest.Notifications.LastAcDcState = $record.State
        }
    }
}

try {
    Save-Manifest
    Write-Host "Preparing read-only lid notifications. Keep lid OPEN. Private results: $run"
    $writer = [IO.StreamWriter]::new((Join-Path $run 'notifications.jsonl'), $false, [Text.UTF8Encoding]::new($false))
    $writer.AutoFlush = $true
    if (-not ('Dell9310.LidNotificationListener' -as [type])) { Add-Type -TypeDefinition $nativeCode }
    $listener = [Dell9310.LidNotificationListener]::new()
    $listener.Start()
    $manifest.Registrations = @($listener.Registrations)
    Flush-Notifications
    if (@($manifest.Registrations | Where-Object { -not $_.Registered }).Count -ne 0) {
        throw 'Power-setting registration failed. Review the registration results; no physical test was requested.'
    }
    $ready = [datetime]::UtcNow
    $deadline = $ready.AddSeconds($Seconds)
    $manifest.ReadyUtc = $ready.ToString('o')
    $manifest.ObservationDeadlineUtc = $deadline.ToString('o')
    $manifest.State = 'observing'
    Save-Manifest
    $writer.WriteLine((ConvertTo-Json -InputObject ([ordered]@{ Kind = 'ready'; TimestampUtc = $manifest.ReadyUtc; BaselineOnly = [bool]$BaselineOnly }) -Compress))
    if ($BaselineOnly) {
        Write-Host "Baseline recording ready ($($manifest.ReadyUtc)). Keep lid open; no physical action is requested."
    } else {
        Write-Host "Lid notifications ready / START ($($manifest.ReadyUtc)). Close normally for 10 seconds, then reopen promptly."
        Write-Host 'Keep AC/devices/policy unchanged. Wait for Lid notifications ENDED. Existing sleep policy remains active.'
    }
    while ([datetime]::UtcNow -lt $deadline) {
        Flush-Notifications
        Start-Sleep -Milliseconds 100
    }
    $manifest.ObservationEndUtc = [datetime]::UtcNow.ToString('o')
    $manifest.State = 'observation_finished'
} catch {
    $manifest.State = 'incomplete'
    $manifest.Error = $_.Exception.Message
    Write-Warning "Lid notification capture error: $($manifest.Error)"
} finally {
    if ($null -ne $listener) {
        $listener.Stop()
        $manifest.Registrations = @($listener.Registrations)
        try { Flush-Notifications } catch { $manifest.State = 'incomplete'; $manifest.Error = $_.Exception.Message }
        if (@($manifest.Registrations | Where-Object { $_.Registered -and $_.UnregisterResultCode -ne 0 }).Count -ne 0) {
            $manifest.State = 'incomplete_unregistration'
        }
    }
    $manifest.FinishedUtc = [datetime]::UtcNow.ToString('o')
    if ($null -ne $writer) {
        try {
            $writer.WriteLine((ConvertTo-Json -InputObject ([ordered]@{ Kind = 'ended'; TimestampUtc = $manifest.FinishedUtc; State = $manifest.State }) -Compress))
        } finally { $writer.Dispose() }
    }
    Save-Manifest
    Write-Host "Lid notifications ENDED ($($manifest.FinishedUtc)). $($manifest.State)"
    Write-Host "Results: $run"
}

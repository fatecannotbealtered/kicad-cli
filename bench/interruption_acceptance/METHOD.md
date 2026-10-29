# Owned Windows x64 interruption controller

This is external method qualification, not product behavior or real KiCad acceptance.
Baseline: `b67eb6e65037d1c0af2bf142777e3dac1534ca6b`.

Create a new unnamed kill-on-close Job. Launch an explicitly supplied new x64
process with `DEBUG_PROCESS | CREATE_SUSPENDED | CREATE_NO_WINDOW` and
STARTUPINFOEX's JOB_LIST and HANDLE_LIST attributes, assigning the Job atomically
at creation and inheriting only stdin/stdout/stderr. Verify membership before
resume. No attach, elevation, global process/thread enumeration or native
product invocation occurs in the qualification. Descendants are observed through
CREATE/EXIT debug events and Job membership, including whatever helper images
actually exist. CREATE process handles are duplicated for final signaled proof.

Parse the named non-forwarded WriteFile export from the trusted local
System32/KernelBase.dll AMD64 PE. Record its disk SHA-256. Observe the actual
KernelBase load event path and base separately. Address = observed base + export
RVA. This does **not** authenticate the loaded instruction bytes.

Install DR0 execution breakpoint on each known thread at KernelBase LOAD_DLL,
reassert at the one initial ntdll loader breakpoint, and install on every later
CREATE_THREAD before continuing. CONTEXT storage is 1232 bytes, 16-byte aligned;
flags AMD64|CONTROL|INTEGER|DEBUG_REGISTERS. DR0/DR6/DR7 offsets are 72/104/112;
EFlags 68, RCX 128, RDX 136, R8 184, RIP 248. Readback must verify DR0/DR7.

Observed loader caveat: on this host, after the initial breakpoint is resumed,
the initial thread's saved CONTEXT is restored with DR0/DR7 cleared. Therefore
reconcile known threads at each later LOAD_DLL/UNLOAD_DLL/CREATE_THREAD/debug
output event while that process is stopped, recording any repair. There is no
timer. A process which restores its context and writes success without a later
debug event can be missed and will never qualify. No universal interception or
loaded-byte authenticity claim is made.

Handle only first-chance SINGLE_STEP with DR6.B0, no other active DR6 cause,
RIP equal to this process's own address, and matching DR0/DR7. For unrelated
WriteFile calls, preserve execution breakpoint and set RF, clear DR6, continue.
Never use a disabled-breakpoint call window. Other application exceptions receive
DBG_EXCEPTION_NOT_HANDLED. Only the initial observed ntdll loader breakpoint is
consumed as debugger bootstrap.

Filter RCX by the inherited stdout handle number, then duplicate that remote
handle and use CompareObjectHandles against a retained reference to the actual
stdout pipe write object. This rejects same-number file/IPC handle reuse. Read
the bounded RDX/R8D buffer and apply a caller-supplied success predicate to one
complete JSON write (at most 65536 bytes). Raw buffers and actual pipe
bytes stay in a protected DACL directory accessible to the current user/SYSTEM.
Public reports contain only hashes/counts. Raw command lines and launch argv
also remain private, with hashes binding the public attribution. No intercepted buffer is forwarded
to the simulated caller. Refused boundaries can naturally write their own output.

While the affected process's debug event is held (its threads stopped), call a
read-only stage witness for the exact operation, saved own output and committed
SQLite receipt; also require zero actual stdout pipe bytes. This does not claim
other Job processes are simultaneously stopped. Recheck every live Job member's
CREATE-bound image, command line, creation FILETIME, parent instance and own Job
membership using held handles; verify known protected processes are outside the
Job and live. Historical parent instances remain birth-bound even if the parent
has naturally exited. Only then TerminateJobObject. Require every process EXIT event,
every duplicated process handle signaled, matching debug-event/handle exit codes,
and matching requested termination exit for every member live at the stop,
Job active count zero, empty membership and actual caller stdout zero.

Timeout, natural exit, stage mismatch, partial observation and controller errors
are never boundary hits. Unexpected second-chance or C0000409/C0000602 exceptions
permanently mark safety_stop and clean only the own Job; ordinary first-chance
exceptions are still handed back to the application. Errors may clean only the new owned Job. No real retry,
automatic timer substitute, product hook, disk patch or success-contract change.
The stage proves process interruption, not power-loss durability/fsync.

An independent outer finally owns native resource closure. Evidence/report
writes cannot bypass closing the new kill-on-close Job. If the inner observer
raises before complete final accounting, this guard requests termination only
on that Job, returns a held exception to the application, briefly drains owned
debug events and closes every retained native handle. No success report is
published until this guard has completed. Reporting failures remain failures.

The finite fixture matrix is frozen with source hashes and expectations before
each round. All historical failed round directories remain. Use an installed
Python host with -B -S and a newly created empty pycache_prefix; -B by itself
does not disable existing bytecode reads.

Primary API references:
- https://learn.microsoft.com/en-us/windows/win32/debug/debugging-events
- https://learn.microsoft.com/en-us/windows/win32/api/debugapi/nf-debugapi-continuedebugevent
- https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-context
- https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setthreadcontext
- https://learn.microsoft.com/en-us/cpp/build/x64-calling-convention

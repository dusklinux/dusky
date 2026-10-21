# Focused logs, app hangs and service isolation

Use working USB SSH first; these procedures do not require Frida. Confirm the
current process, command paths and installed help. The commands below were
exercised on the documented iOS 16.7.16 device; verify availability elsewhere.
For daemon targets, backups and native override semantics, use the separate
[daemon operating guide](../../daemons/README.md).

## Save the state that a test might change

Record the affected jobs' actual labels, plist paths, domains, saved overrides
and registration states. An enabled on-demand job need not have a running PID.
Back up relevant preferences and override files with ownership/mode before
mutations, keeping the original baseline separate from later snapshots. Use
launchctl to change overrides; do not replace its database directly.

Native `/var/mobile` and bootstrap `/var/jb/var/mobile` preference locations can
differ. Discover the owning app's actual file. An interactive sudo shell does
not elevate another SSH channel. Use the phone's own Python runtime for remote
scripts; the host CLI's Python version requirement is separate.

## Capture one reproduction

On Linux, check `idevicesyslog --help`, then in a dedicated terminal:

```sh
diagnostic_dir=$(mktemp -d "${TMPDIR:-/tmp}/iphone-diagnostics.XXXXXXXX")
idevicesyslog --no-colors -p 'Preferences|akd|appleaccountd|imagent' \
  -o "$diagnostic_dir/repro.log"
```

`-p` takes a pipe-separated process list; adjust it to the failing path. For
carrier delivery, include CommCenter and imagent; add MobilePhone/CMFSyncAgent
for contact/filter queries. Keep the list and duration narrow. Record when the
user performs each action, then stop this owned relay with Ctrl-C. USB relay
streaming itself can consume substantial diagnosticd CPU, so pause it before
measuring normal CPU behavior. Reconnect/start a new owned capture after a
userspace restart; the old relay can stop producing fresh data.

If the relay cannot collect, inspect `com.apple.diagnosticd` before assuming
Frida is needed. In a root phone shell, using the confirmed launchctl path:

```sh
/var/jb/usr/bin/launchctl print-disabled system
/var/jb/usr/bin/launchctl print system/com.apple.diagnosticd
```

When temporary collection requires this service, save its original state,
enable it and bootstrap its existing plist only if unregistered:

```sh
/var/jb/usr/bin/launchctl enable system/com.apple.diagnosticd
/var/jb/usr/bin/launchctl bootstrap system /System/Library/LaunchDaemons/com.apple.diagnosticd.plist
```

Do not enable an entire diagnostics group merely to obtain this relay. After
stopping the relay, restore the saved override and registration state: a service
originally disabled/unregistered needs both disable and bootout, with readback.
Preserve a service that was already enabled or registered. For feature batches,
choose and verify session apply or userspace restart as described below.

The phone lacked an executable unified `log` CLI during these checks. In zsh,
`command -v log` can find a shell builtin; use `type log` and confirm an executable
path before trying macOS-style `log show` arguments. Logs can contain account or
message data; keep raw captures outside published source and report only the
evidence needed for the diagnosis.

## Sample a frozen app before restarting it

A blocked main queue may never answer a Frida UI probe. Discover a fresh PID
with `ps -A -o pid,comm` and collect a short native sample over SSH instead.
First create a private output directory as mobile, from Linux:

```sh
sample_dir=$(mktemp -d "${TMPDIR:-/tmp}/iphone-stack-sample.XXXXXXXX")
phone_capture_dir=$(ssh -o BatchMode=yes -p 2222 mobile@127.0.0.1 \
  'mktemp -d /var/mobile/Documents/control-diagnostics.XXXXXXXX')
printf '%s\n' "$phone_capture_dir"
```

Use the working key/master route from BOOTSTRAP.md. In the **root phone shell**,
replace the two placeholders below with that returned directory and fresh PID:

```sh
capture_dir=/var/mobile/Documents/control-diagnostics.REPLACE_WITH_RETURNED_SUFFIX
target_pid=REPLACE_WITH_FRESH_PID
/usr/sbin/spindump "$target_pid" 3 10 -onlyTarget -noIPC \
  -o "$capture_dir/target.spin.txt"
/usr/sbin/spindump -i "$capture_dir/target.spin.txt" -symbolicate -noIPC \
  -noBinary -o "$capture_dir/target.symbols.txt"
```

This samples only the target for three seconds at 10 ms intervals, then
symbolicates the saved report on the device. Sampling defaults to unsymbolicated
output on this build; `???` frames are not enough to identify the waiting API.
Symbolication produced useful names even with coresymbolicationd disabled;
inspect the output before enabling another collector because of a connection
warning. `-noIPC` permitted sampling without enabling the spindump launchd job.
Check `/usr/sbin/spindump -h` before changing flags. Do not invoke it without
arguments: that defaults to broader system sampling.

Back on Linux, in the terminal holding `phone_capture_dir`:

```sh
scp -P 2222 "mobile@127.0.0.1:$phone_capture_dir/target.symbols.txt" \
  "$sample_dir/target.symbols.txt"
```

Inspect main and any queue/thread it is waiting for. Follow the whole wait chain:
main waiting for a rendering queue, which waits in a notification operation for
main, indicates a circular wait rather than proof of a disabled network daemon.
Compare relevant installed tweak source/binary before assigning blame. See
[DEVELOPMENT.md](DEVELOPMENT.md#4-add-hooks-deliberately) for the asynchronous
bundle-notification pattern. Preserve the sample before terminating the app;
then verify any fix with a fresh process and the actual user action.

## Separate the failure stages

For sign-in, distinguish transport/password exchange, secondary verification UI,
code entry and account persistence. Successful SRP narrows the problem but does
not prove the next page displayed. A later XPC disconnect after the client exits
can be a consequence of the frozen/killed client. Avoid repeatedly requesting
codes when Apple reports too many attempts; inspect the local UI path first.

For carrier SMS, ask whether it is missing inside Messages or only lacks a
notification. Compare CommCenter receipt with imagent activity and native Messages
database metadata. If necessary, use SQLite read-only URI mode on
`/var/mobile/Library/SMS/sms.db`; query counts/latest row IDs, timestamps and service
rather than message text. Read the live database with its WAL present rather than
copying only sms.db and interpreting a stale snapshot. A modem receipt alone does
not establish that Messages stored or displayed it.

## Narrow service candidates in batches

Group plausible candidates using logs, exported endpoints and existing dependency
notes. Verify saved overrides separately from session registration. When the
installed manager offers Apply Now, it can attempt loading/unloading the saved
batch without a restart. Inspect per-job results and read registration back after
a bounded settle wait: bootout can complete asynchronously. An enabled on-demand
job need not have a running PID, and a registered job does not prove that an
existing client has reconnected. Reopen affected clients or use userspace restart
when needed; saved overrides alone do not establish a live change.

Retry fresh user actions. If a batch works, reduce it by halves instead of testing
every service individually. Keep each test's retained/removed sets and outcomes
explicit, and use the same session-apply/client-reopen or restart conditions for
both halves.

Reproduce with the candidates restored Off before attributing success to an On
batch: restarting clients can itself clear stale failures. Use equivalent restart
conditions for comparisons. Dependencies can interact; if neither half works on
its own, investigate the combination instead of discarding both. Finish by
restoring unproven candidates, preserving the isolated repair and existing user
choices, and comparing the final state with the immediate pre-test baseline.

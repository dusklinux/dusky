# Use this iPhone control manual

- Read README.md for the operating sequence and VALIDATION.md for readiness and
  completion checks. Use BOOTSTRAP.md when SSH/Frida is missing, RECIPES.md for
  tasks, EXTENDING.md for typed APIs, DEVELOPMENT.md for compiled helpers, and
  unlock.md for the no-Frida unlock fallback. Use CONTAINERS.md for app/group
  discovery, termination, preference synchronization, archives and real-app fixtures.
- Use DIAGNOSTICS.md for focused logs, frozen-app stack samples and grouped
  service isolation. Keep project-specific dependency/release results in their
  separate notes rather than turning this manual into a session transcript.
- Prefer USB SSH. Reuse existing authenticated connections/server/receiver when
  available. Do not interrupt another operator’s capture or connection.
- Discover fresh PIDs and wait for injection readiness before attaching after a
  restart. Keep UIKit work in its owning process on the main queue.
- Use the bundled CLI/runtime. Check live private-method encodings; never guess
  a selector, scalar width, block representation, internal offset or object type.
  Use ns() for NSString, utf8() for C strings and explicit 0/1 for native BOOL.
- Use the current bundled `unlock` routing; never call native finish directly
  while Duskie's simulated lock is enabled. An ABI match alone does not prevent
  this semantic-state crash. The screenshot command uses the rotation export.
- A successful request, notification or stale report is not completion. Read the
  actual resulting state and preserve user media/queued uploads.
- Stop only owned diagnostics. Respring or restart userspace when necessary;
  never normal-reboot this setup. Diagnose Safe Mode before clearing its marker.
- Restore temporary settings/keep-awake flags and unload owned callbacks/helpers.
  Keep passwords out of command arguments and generated source.
- Maintain this directory as a reusable operating manual: add complete procedures,
  exact ABI requirements and actionable limits. Do not add project release notes,
  review narratives, benchmark reports, session PIDs or temporary checkout paths.
- Local tests: python3 test_control.py; node --check runtime.js;
  node test_runtime.cjs; ./iphone-control --control-path /tmp/iphone-control-ssh doctor. These do not contact the phone.

# Using this iPhone control directory

- Read README.md and VALIDATION.md before operating the phone. BOOTSTRAP.md covers
  missing Frida/root/signing setup; RECIPES.md covers app settings, radios, capture
  transports and preview. SSH, Frida and the USB recording receiver are separate.
  Use the bundled iphone-control/runtime.js instead of rebuilding probes from an old transcript.
- Follow the current user's authorization and the main/side conversation boundary.
  A side task that writes local tooling must not take over a main task's phone,
  server, tunnel or recording. No device operation is implied by running local tests.
- Prefer USB SSH. Discover fresh PIDs. Keep UIKit work on its process's main queue.
  Check the actual private-method ABI; never guess enums or offsets on mismatch.
- Use ns() for NSString arguments, utf8() only for C strings, and 0/1 for native
  BOOL arguments. Keep autorelease/lifetime requirements in EXTENDING.md in mind.
- A request result, notification post, preference label or stale status file does
  not prove an action completed. Verify live state and actual media when relevant.
- SSH/doctor/process discovery have read-only device acceptance. The consolidated
  Frida UI wrapper remains locally tested; inherited successful probes do not
  make those routes device-tested. Record future live results in VALIDATION.md
  with scope/limitations.
- Do not overwrite unlock.md's historical evidence, embed credentials in new code,
  broadly kill processes, reboot normally, or clear Safe Mode without diagnosing
  the incident. Use the established owner recovery preferences and current scope.
- Local checks: python3 test_control.py; node --check runtime.js;
  node test_runtime.cjs; ./iphone-control doctor. These don't contact the phone.

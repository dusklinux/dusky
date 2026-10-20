# Dusky Sites

Targets Firefox 158+ and Python 3.15+ on Linux/Wayland. Website CSS injection
remains off by default; `~/.config/dusky/settings/dusky_sites/config.json` controls `webThemeEnabled`,
`forceUnthemedWebsites`, `disabledSites`, `colorsPath`, and `websitesDir`.
Extension-only options, including eco mode and browser-theme control, belong in
`extension/defaults.js`. The content color scheme now follows the palette by
default (`auto`); an explicit override still wins.

The native port itself keeps Firefox 158's event page alive. Host `keepAlive`
is therefore off by default; setting it to true still requests periodic host
pings. The extension retains connection-health probes and a revival alarm.

## Development checks

Run from this directory:

```sh
node --test tests/extension.test.mjs
python3 -X dev -W error -m unittest discover -s tests -v
bash -n install-dusky-sites.sh
bash -n dusky-burst.sh
shellcheck install-dusky-sites.sh dusky-burst.sh
```

`install-dusky-sites.sh` installs the development native host, checks its framed
protocol, and creates `dusky_sites-unsigned.zip` for signing. It resolves its
source directory relative to itself, so it can be called from another working
directory. The smoke test has response deadlines and supports chunked frames.

`dusky-burst.sh [palette.css] [count] [interval-seconds]` rewrites actual palette
colors atomically, including namespaced Matugen declarations. It follows an
existing palette symlink and restores the target's original bytes and file mode
on completion or SIGINT/SIGTERM/SIGHUP. Run it without another palette writer
active; forced termination with SIGKILL cannot execute cleanup.

## Release packaging

The audited source version is **6.2.2**, with paint-cache schema 8. Existing
schema-7 paint entries are invalidated on upgrade. The preserved signed
`xpi/dusky_sites.xpi` is **6.2.1** and does not include these JavaScript fixes.

Submit `dusky_sites-unsigned.zip` to Mozilla for an unlisted signature, then
replace `xpi/dusky_sites.xpi` with the returned signed 6.2.2 package. Re-run the
main Firefox theme setup and its source/file audit after replacing it. The main
setup deliberately refuses a signed package that differs from the source.
Do not replace the signed XPI with an unsigned ZIP or alter files inside it:
Firefox release verifies the signature.

For local rendering tests, load `extension/manifest.json` as a temporary add-on
in an isolated profile through `about:debugging`. Temporary installation is a
development test and does not establish that permanent signed installation
works. The existing signed package and real Firefox profile are preserved until
the signed update is available.

Mozilla signing documentation:
https://extensionworkshop.com/documentation/publish/signing-and-distribution-overview/

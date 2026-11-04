# Dusky Network

Dependency-free Rust network meter shared by Waybar and Dusky Glance.

Install or refresh the network meter:

```sh
python3 ~/user_scripts/arch_setup_scripts/scripts/154_dusky_network_meter_setup.py
```

Setup reuses a verified native build or matching ISO package, and recompiles
changed sources on its next invocation. `--force-rebuild` requests a native build.
Compiler updates and edits to the build/storage scripts do not invalidate an
otherwise matching installed binary; use `--force-rebuild` to refresh it deliberately.
Compilation is frozen/offline, preferring executable tmpfs with disk fallback;
build artifacts use temporary storage outside this source directory. The installed
binary, manifest and build lock live in
`~/.local/share/dusky/dusky_network/`; the launcher is `~/.local/bin/dusky-network`.
`Cargo.lock` is Cargo metadata required for frozen builds.

The ISO factory recipe builds a generic x86-64 package with the same setup file
and an explicit output directory outside the source tree. Step 130 installs and
enables `dusky_network.service`.

The sampler runs at one second when connected and sleeps three seconds when no
usable interface exists. It measures link traffic, including LAN traffic during
WAN outages. `watch --horizontal` and `watch --vertical` emit Waybar JSON;
`osd MODE` emits plain text. The service retries failures every three seconds.

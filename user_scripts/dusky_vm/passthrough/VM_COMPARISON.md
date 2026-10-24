# Manual and automated Linux VM comparison

Observed on this host, 2026-10-24. These are configuration and integration checks,
not a benchmark proving one setup faster for every workload.

| Area | `dusky_manual` | `dusky-linux-test` | Decision |
|---|---|---|---|
| Firmware/platform | OVMF UEFI, Q35 | OVMF UEFI, Q35 | Both use the intended modern platform. |
| CPU | 4 vCPUs, host passthrough | 4 vCPUs, host passthrough, explicit single-socket topology | Keep host passthrough; no affinity tuning without a workload measurement. |
| RAM | 4024 MiB | 4096 MiB | No meaningful optimization conclusion from this small difference. |
| Disk | qcow2 on `/mnt/zram1`, VirtIO | qcow2 on `/mnt/zram1`, VirtIO, explicit RAM-appropriate caching, io_uring and discard | Keep the tested automated storage setup. |
| Network | Direct bridge attachment to `virbr0` | Libvirt network `default`, whose bridge is `virbr0` | Same NAT network/data path. The network object gives better lifecycle/DHCP visibility. |
| Graphics | VirtIO 2D | Changed from VirGL 3D to VirtIO 2D | Borrowed the manual display configuration after repeated shutdown hangs with 3D. Two 2D shutdown/restart cycles passed, including one from the desktop; accelerated rendering remains opt-in. |
| Boot order | Disk first | Generator defaults to disk first; the older test VM definition still had empty CD-ROM first | Aligned the existing test VM's saved definition with disk-first boot. No running-guest hardware change was needed. |
| Guest agent | Channel exists; agent unavailable during inspection | Agent active | Automatic integration uses QGA. Manual clipboard setup works over SSH without requiring it. |
| Shared files | No VirtioFS device in the inspected definition | Host `/mnt/zram1` mounted at guest `/media/host` | Keep the tested automated sharing configuration. |
| Clipboard at investigation start | No host bridge running | No host bridge running | Replaced detached startup with supervised host services for both VMs. |

`virbr0` is the host's bridge for libvirt's default private NAT network. The host
has `192.168.122.1`; guests get private addresses on the same subnet. That is why
the host can SSH directly to their addresses, while NAT handles guest outbound
traffic. Attaching by the network's name does not add another virtualization
layer compared with naming that same bridge directly.

No manual guest scripts were copied into the host workflow. Canonical clipboard
code, setup, configuration, keys and service definitions remain on the host;
the guest receives a temporary execution copy. The manual VM's hardware settings
were retained. Its SSH account received the per-VM public key for automatic
clipboard transport.

Host commands for the two configured VMs:

```sh
python3 vm_clipboard.py status dusky_manual
python3 vm_clipboard.py status dusky-linux-test
```

Both services must report `Clipboard ready: host ↔ guest (text)` before expecting
copy-and-paste in the logged-in Hyprland sessions. No guest-side command is
required. The host user manager restarts the services after failures and starts
them again on future host logins.

## Validation

After correcting the clipboard ordering race, three combined live runs passed:
60 directional text/code actions, 120 recipient checks and twelve transfers of
approximately 2 MiB, alongside 3 GiB of large shared-file writes, 9,600 concurrent
file creations and 6,000 atomic replacements. SHA-256 checks, opposite-side
deletion, sparse files, links, permissions and renames passed. Clipboard recipient
verification medians were 100–103 ms; these include SSH test overhead and do not
measure interactive application paste latency. Original host clipboard contents
were restored without being printed in the logs.

The supervisor recovered automatically after SIGKILL; a guest endpoint recovered
after SIGTERM. The reboot checks verified clean shutdown, waiting while the VM
was stopped and automatic SSH reconnection. Clipboard readiness after a guest
Hyprland login was verified without running a guest command. The focused suite
passes 25 tests, including delayed-reply ordering over real pipes/selectors.

These checks used the supplied guest's kernel 7.2.11 and Python 3.14.8, with
Python 3.15 on the host. They do not certify the final, unshipped ISO baseline of
kernel 7.3+; repeat integration checks on that final image. Two successful 2D
cycles support the default change, but do not prove VirGL caused the original
guest kernel/Plymouth hang. Desktop rendering performance was not benchmarked.

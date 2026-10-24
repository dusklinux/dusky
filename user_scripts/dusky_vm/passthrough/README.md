# Dusky virtual machines

Run the shared profile launcher from this directory:

```sh
./000_dusky_vm.py --linux
./000_dusky_vm.py --windows
./000_dusky_vm.py --linux --dry-run
```

The root contains entry points and tools shared by both guest operating systems.
OS-specific implementations, clipboard transports, documentation and tests have
their own directories:

```text
passthrough/
├── 000_dusky_vm.py                 Profile launcher
├── 05_virtio_iso.py                Shared hypervisor/package staging
├── 07_storage_setup.py             Shared storage provisioning
├── 10_virt_modular_daemon.py        Shared libvirt setup
├── 15_gpu_probing_kernal_param_mkinit.py  Shared GPU passthrough preparation
├── 20_networking_nmcli.py          Shared networking
├── 30_kvm_vm_deploy.py             Linux/Windows deployment dispatcher
├── 35_cpu_pinning_generator.py     Shared CPU pinning
├── nvidia_vfio_bind_unbind.py      Shared GPU binding utility
├── profile/                       Linux and Windows task sequences
├── linux/
│   ├── deploy_vm.py               Linux VM deployment
│   └── guest_integration.py       Linux share/native clipboard setup
├── windows/
│   ├── deploy_vm.py               Windows VM deployment
│   ├── 25_looking_glass.py         Host Looking Glass setup
│   ├── 50_win.py                   Windows VM manager
│   ├── 55_rdp.py                   Windows RDP helper
│   ├── 60_configure_client_ini.py  Looking Glass client configuration
│   └── guest/                     Scripts to run inside Windows
├── clipboard/
│   ├── vm_clipboard.py            SSH clipboard supervisor/setup
│   ├── wayland_clipboard.py       Wayland SSH clipboard transport
│   └── native_agent/              Native SPICE agent recipe and patches
├── docs/
│   ├── PROFILES.md                Runner usage and profile format
│   ├── linux/                    Linux workflow and integration notes
│   └── windows/                  Windows guides and handover notes
└── tests/                         Regression and sharing stress tests
```

Shared tools stay at the root even when only one supplied profile currently
uses them. For example, GPU passthrough and CPU pinning can also apply to Linux
guests. The Windows folder contains the Windows/Looking Glass workflow rather
than duplicating the shared host setup.

- [Profile commands, resume behavior and configuration](docs/PROFILES.md)
- [Linux deployment and SSH clipboard workflow](docs/linux/WORKFLOW.md)
- [Native Linux guest clipboard integration](docs/linux/NATIVE_GUEST_INTEGRATION.md)
- [Manual/automated VM comparison](docs/linux/VM_COMPARISON.md)
- [Windows guest setup guide](docs/windows/VM_SETUP_GUIDE.md)
- [Windows handover notes](docs/windows/HANDOVER_NOTES.md)

The SSH clipboard bridge and native SPICE agent are separate integration
implementations. Their setup/source files are grouped together under
`clipboard/`; their respective usage remains documented in the Linux guides.
Windows guest bootstrap and Python helpers stay together under `windows/guest`
so the bootstrapper can find its sibling scripts.

Direct host clipboard management now uses:

```sh
python3 clipboard/vm_clipboard.py status DOMAIN
```

The shared deployment command remains `python3 30_kvm_vm_deploy.py`; its default
is Linux, and `--windows` selects `windows/deploy_vm.py`. Profile commands and
forwarding VM arguments after `--` retain the same interface.

The `win`, `nvidia_bind` and `nvidia_unbind` shell definitions use the new paths.
Open a new terminal to load the updated definitions in an existing session.

### Managing Swap on a Running System

These commands are for managing swap after you have booted into your installed Arch Linux system.

#### Checking Swap Status

To verify that your swap is active and see its usage, you can use one of two commands:

*   **List active swap devices:**
    ```bash
    swapon --show
    ```
    **Example Output:**

| NAME | TYPE | SIZE | USED | PRIO |
|---|---|---|---|---|
| /dev/sda3 | partition | 8G | 0B | -2 |

*   **View total memory and swap usage:**
    ```bash
    free -h
    ```
    **Example Output:**

| total | used | free | shared | buff/cache | available |
|---|---|---|---|---|---|---|
| Mem: | 15Gi | 2.1Gi | 8.2Gi | 123Mi | 5.1Gi | 12Gi |
| Swap: | 8.0Gi | 0B | 8.0Gi | | | |

#### Temporarily Activating/Deactivating Swap

You can manually enable or disable swap spaces for the current session without rebooting.

*   **To activate a specific swap partition:**
    ```bash
    sudo swapon /dev/sdXN
    ```

*   **To deactivate a specific swap partition:**
    ```bash
    sudo swapoff /dev/sdXN
    ```

*   **To deactivate all active swap spaces:**
    ```bash
    sudo swapoff -a
    ```

#### Preventing activation at boot

Remove or comment out the relevant `/etc/fstab` entry, then reload systemd's configuration and inspect the active swap areas:

```bash
sudo systemctl daemon-reload
swapon --show
```

Removing an entry does not deactivate swap already in use; use `swapoff` on the specific area if desired and if enough memory or other swap is available. A zram-generator device is controlled by its generator configuration, not necessarily fstab. A partition may also be activated through another mechanism, including systemd's GPT auto-discovery; investigate the responsible unit and configuration if it reappears. Mask a specific unit only when that is the intended way to override its activation. Do not copy another machine's unit name.

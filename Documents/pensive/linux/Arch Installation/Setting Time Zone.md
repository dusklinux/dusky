
Configuring the correct time zone is a crucial step for your new system. It ensures the clock displays the correct local time; timezone selection affects local display, while network and signature checks depend on the system clock being accurate.

> [!NOTE]
> These commands should be executed from within the `chroot` environment after you have installed the base system.

### 1. Set the Time Zone

You will set the system's time zone by creating a symbolic link from your chosen zone file to `/etc/localtime`.

> [!TIP] How to Find Your Time Zone
> If you're unsure of the exact path for your time zone, you can list the available regions and cities within the `/usr/share/zoneinfo` directory.
> ```bash
> # First, list the available regions (e.g., America, Europe, Asia)
> ls /usr/share/zoneinfo/
> 
> # Then, list the cities within your region
> ls /usr/share/zoneinfo/Asia/
> ```

Once you have identified your `Region/City`, use the following command to create the symbolic link. Replace `Asia/Kolkata` with your specific time zone.

```bash
ln -sf /usr/share/zoneinfo/Asia/Kolkata /etc/localtime
```

### 2. Synchronize the Hardware Clock

After ensuring the system clock is correct, you can write it to the hardware clock (RTC). This command sets the hardware clock from the system clock and updates `/etc/adjtime` where applicable.

```bash
hwclock --systohc
```

> [!IMPORTANT]
> `hwclock` consults `/etc/adjtime` to determine the RTC timescale; without that file it defaults to UTC. A system sharing its RTC with an OS configured for local time needs a consistent convention across both systems. This command changes the machine’s RTC, not just the target filesystem.


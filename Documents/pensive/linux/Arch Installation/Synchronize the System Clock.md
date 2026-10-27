# Check the live system clock

Correct time matters for certificate and package-signature validity. Run this in the live environment:

```bash
timedatectl status
```

On a networked live system with an available time synchronization service, enable synchronization if needed:

```bash
timedatectl set-ntp true
timedatectl status
```

An enabled service is not proof that synchronization succeeded: check the reported synchronization state and network access. Offline installation can proceed without NTP if the clock is already correct; otherwise set it using a reliable time source. Changing the timezone changes how time is displayed, not the underlying instant. Set the **installed system's** timezone separately in [[Setting Time Zone]].

# Create a user account

Run these commands inside the installed system's chroot. Replace `your_username` with the intended login name:

```bash
useradd -m -G wheel your_username
passwd your_username
```

`-m` creates a home directory; `-G wheel` adds the account to the supplementary `wheel` group. Membership alone does not grant sudo access: install and configure sudo as described in [[Configuring Sudo Privileges]]. `passwd` without a username changes the current account's password, which is root in this chroot.

Verify the account and its groups:

```bash
id your_username
```

Add other groups only when the relevant software or device setup needs them. Many device permissions are assigned to the active session by logind and udev; the older blanket list of `audio`, `video`, `input`, and other groups is not universally required. Check available groups with `getent group`. See [ArchWiki: users and groups](https://wiki.archlinux.org/title/Users_and_groups).



To allow users in the `wheel` group to execute commands with root privileges, you must edit the `/etc/sudoers` file. This is a critical step for enabling system administration from a non-root user account.

Install `sudo` in the target if it is not already installed (`pacman -S sudo` using the configured target repositories). Commands below run inside the target chroot.

#### 1. Edit the `sudoers` File

The safest way to modify the `sudoers` configuration is with the `visudo` command, which validates the file's syntax before saving. You can specify your preferred text editor using the `EDITOR` environment variable.

```bash
EDITOR=nvim visudo
```

> [!WARNING] Always Use `visudo`
> Never edit the `/etc/sudoers` file directly with a standard text editor. The `visudo` command performs a syntax check upon saving, which prevents errors that could lock you out of `sudo` and potentially your system.

#### 2. Enable the `wheel` Group

Inside the editor, find the line that grants sudo access to the `wheel` group. It is commented out by default.

**Locate this line:**
```ini
# %wheel ALL=(ALL:ALL) ALL
```

**Uncomment it** by removing the leading `#` symbol:
```ini
%wheel ALL=(ALL:ALL) ALL
```

> [!NOTE] Understanding the `sudoers` Rule
> This line grants extensive privileges. Here's what it means:
> - `%wheel`: The rule applies to all users in the `wheel` group.
> - `ALL=`: The rule applies on any host.
> - `(ALL:ALL)`: Users can run commands as any target user or group.
> - `ALL`: The rule applies to all commands.

#### 3. Save and Exit

After uncommenting the line, save your changes and exit the editor. Users who are members of the `wheel` group (as configured in [[User Account Creation]]) will now be able to use `sudo`.


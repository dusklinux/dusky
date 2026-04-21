downlaod the following tweaks
python 3.14.3
openssh
gawk
filza
icleanerpro
powerselector

on newterm do this first sudo passwd root and change the password to the same as the one you set earlier in dopamine at the time of jailbreakig for the first time. z

```ini
scp /home/dusk/Documents/pensive/linux/Important\ Notes/IOS/daemons/scripts/daemons/daemonmanager root@192.168.29.75:/var/jb/basebin/
```
```ini
scp /home/dusk/Documents/pensive/linux/Important\ Notes/IOS/daemons/scripts/daemons/daemon.cfg root@192.168.29.75:/var/jb/basebin/
```

ssh reboot command
```ini
launchctl reboot userspace
```

to apply the list
```ini
/var/jb/basebin/daemonmanager apply
```
to revert
```ini
/var/jb/basebin/daemonmanager reset
```



neovim delete lines with no in it

```ini
:g/ no/d
```
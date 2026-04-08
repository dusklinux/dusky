1. dont run the offline package isntaller from the root of /mnt/zram cuz the `lost+found` dir will cause issues because of permissions issues

download these pacakges first 

```bash
sudo pacman -Syu --needed --noconfirm archiso
```
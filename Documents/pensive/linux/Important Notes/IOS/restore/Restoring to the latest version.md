install the utitlity package
```bash
paru -S idevicerestore-git
```

mind the path and the name of the latest ipsw version. 
```
sudo idevicerestore -C /mnt/zram1/cache/ -e /mnt/zram1/iPhone_5.5_P3_16.7.12_20H364_Restore.ipsw
```

latest iso ipsw path , fresh install, clean wipe
```bash
sudo idevicerestore -y -e -C /mnt/zram1/cache/ /mnt/zram1/iPhone_5.5_P3_16.7.15_20H380_Restore.ipsw
```
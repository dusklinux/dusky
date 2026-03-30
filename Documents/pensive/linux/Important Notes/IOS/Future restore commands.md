
```
sudo pacman -Syu usbmuxd
```

```
sudo systemctl start usbmuxd
```

pelrain recovery mode:

```bash
paru -S --needed palera1n --noconfirm
```

```bash
palera1n -D
```
```bash
palera1n -h
```
or download the binary and make it exicutable 
```bash
sudo chmod u+x palera1n
```

```
sudo /mnt/zram1/future_restore/iphonee/palera1n-linux-x86_64 -D
```


gaster : (to boot untrusted images)
make sure it's exicutable first 
```bash
sudo chmod u+x gaster
```

```
sudo /mnt/zram1/future_restore/iphonee/gaster pwn
```

gaster " to find the usb handle
```
sudo /mnt/zram1/future_restore/iphonee/gaster reset
```

to set nonce
make sure it's exicutable first 
```bash
sudo chmod u+x futurerestore
```

this will need to be run several times cuz it fails on the first try sometimes. 
```
sudo ./futurerestore -t /mnt/zram1/future_restore/iphonee/4878275665063854_iPhone10,2_d21ap_16.6-20G75_27325c8258be46e69d9ee57fa9a8fbc28b873df434e5e702a8b27999551138ae.shsh2 --use-pwndfu --set-nonce --latest-sep --latest-baseband /mnt/zram1/future_restore/iphonee/iPhone_5.5_P3_16.6_20G75_Restore.ipsw
```

now, if it doesn't automatcially go into recovery mode, go into it manually by button combo. vol up vol down and power hold. and you should see the pc and cable logo on the iphone. 

restart usbmxd service and leve the terminal open
```
sudo systemctl stop usbmuxd && sudo usbmuxd -p -f
```

futurerestoring main 
, this also needs to be run a few times before it suceeds, sometimes the phone needs to be disconnected and quickly reconneced during the process, like at the place it gets stuck is where the phone should quickly be reconneced
```
sudo ./futurerestore -t /mnt/zram1/future_restore/iphonee/4878275665063854_iPhone10,2_d21ap_16.6-20G75_27325c8258be46e69d9ee57fa9a8fbc28b873df434e5e702a8b27999551138ae.shsh2 --latest-sep --latest-baseband /mnt/zram1/future_restore/iphonee/iPhone_5.5_P3_16.6_20G75_Restore.ipsw
```


> [!NOTE]- sucessful logs
> ```ini
> future_restore/iphonee ❯ sudo ./futurerestore -t /mnt/zram1/future_restore/iphonee/4878275665063854_iPhone10,2_d21ap_16.6-20G75_27325c8258be46e69d9ee57fa9a8fbc28b873df434e5e702a8b27999551138ae.shsh2 --latest-sep --latest-baseband /mnt/zram1/future_restore/iphonee/iPhone_5.5_P3_16.6_20G75_Restore.ipsw
> Version: v2.0.0(22a5d89bdec6e1c441012d63822184c93530b7e5-321)
> img4tool version: 0.197-aca6cf005c94caf135023263cbb5c61a0081804f-RELEASE
> libipatcher version: 0.91-cb10d973d0af78cc55020d4cf1187c28fad0f2a0-RELEASE
> Odysseus for 32-bit support: yes
> Odysseus for 64-bit support: yes
> INFO: device serial number is F2LVDW1BJCLP
> [INFO] 64-bit device detected
> futurerestore init done
> reading signing ticket /mnt/zram1/future_restore/iphonee/4878275665063854_iPhone10,2_d21ap_16.6-20G75_27325c8258be46e69d9ee57fa9a8fbc28b873df434e5e702a8b27999551138ae.shsh2 is done
> User specified to use latest signed SEP
> [Error] failed to download file from=https://api.ipsw.me/v2.1/firmwares.json/condensed to=/tmp/firmwares.json CURLcode=28
> futurerestore: failed with exception:
> [exception]:
> what=[TSSC] parsing firmware.json failed
> 
> code=108527670
> line=1656
> file=/tmp/Builder/repos/futurerestore/src/futurerestore.cpp
> commit count=321
> commit sha  =22a5d89bdec6e1c441012d63822184c93530b7e5
> future_restore/iphonee ❯ sudo ./futurerestore -t /mnt/zram1/future_restore/iphonee/4878275665063854_iPhone10,2_d21ap_16.6-20G75_27325c8258be46e69d9ee57fa9a8fbc28b873df434e5e702a8b27999551138ae.shsh2 --latest-sep --latest-baseband /mnt/zram1/future_restore/iphonee/iPhone_5.5_P3_16.6_20G75_Restore.ipsw
> Version: v2.0.0(22a5d89bdec6e1c441012d63822184c93530b7e5-321)
> img4tool version: 0.197-aca6cf005c94caf135023263cbb5c61a0081804f-RELEASE
> libipatcher version: 0.91-cb10d973d0af78cc55020d4cf1187c28fad0f2a0-RELEASE
> Odysseus for 32-bit support: yes
> Odysseus for 64-bit support: yes
> INFO: device serial number is F2LVDW1BJCLP
> [INFO] 64-bit device detected
> futurerestore init done
> reading signing ticket /mnt/zram1/future_restore/iphonee/4878275665063854_iPhone10,2_d21ap_16.6-20G75_27325c8258be46e69d9ee57fa9a8fbc28b873df434e5e702a8b27999551138ae.shsh2 is done
> User specified to use latest signed SEP
> Using cached SEP.
> Checking if SEP is being signed...
> Sending TSS request attempt 1... response successfully received
> SEP is being signed!
> User specified to use latest signed baseband
> Using cached Baseband.
> Checking if Baseband is being signed...
> Sending TSS request attempt 1... response successfully received
> Baseband is being signed!
> Downloading the latest firmware components...
> Downloading SE firmware
> 088 [==============================================================================>]
> Checking for cached Cryptex1...
> Using cached Cryptex1,SystemOS.
> Using cached Cryptex1,SystemVolume.
> Using cached Cryptex1,SystemTrustCache.
> Using cached Cryptex1,AppOS.
> Using cached Cryptex1,AppVolume.
> Using cached Cryptex1,AppTrustCache.
> Finished downloading the latest firmware components!
> Found device in Recovery mode
> Device already in recovery mode
> Found device in Recovery mode
> Identified device as d21ap, iPhone10,2
> Extracting BuildManifest from iPSW
> Product version: 16.6
> Product build: 20G75 Major: 20
> Device supports Image4: true
> Got ApNonce from device: 27 32 5c 82 58 be 46 e6 9d 9e e5 7f a9 a8 fb c2 8b 87 3d f4 34 e5 e7 02 a8 b2 79 99 55 11 38 ae 
> checking if the APTicket is valid for this restore...
> Verified ECID in APTicket matches the device's ECID
> checking if the APTicket is valid for this restore...
> Verified ECID in APTicket matches the device's ECID
> [IMG4TOOL] checking buildidentity 0:
> [IMG4TOOL] checking buildidentity matches board ... NO
> [IMG4TOOL] checking buildidentity 1:
> [IMG4TOOL] checking buildidentity matches board ... YES
> [IMG4TOOL] checking buildidentity has all required hashes:
> [IMG4TOOL] checking hash for "AOP"                     OK (untrusted)
> [IMG4TOOL] checking hash for "AVE"                     OK (found "avef" with matching hash)
> [IMG4TOOL] checking hash for "Ap,SystemVolumeCanonicalMetadata"OK (found "msys" with matching hash)
> [IMG4TOOL] checking hash for "AppleLogo"               OK (found "logo" with matching hash)
> [IMG4TOOL] checking hash for "AudioCodecFirmware"      OK (untrusted)
> [IMG4TOOL] checking hash for "BasebandFirmware"        IGN (no digest in BuildManifest)
> [IMG4TOOL] checking hash for "BatteryCharging0"        OK (found "chg0" with matching hash)
> [IMG4TOOL] checking hash for "BatteryCharging1"        OK (found "chg1" with matching hash)
> [IMG4TOOL] checking hash for "BatteryFull"             OK (found "batF" with matching hash)
> [IMG4TOOL] checking hash for "BatteryLow0"             OK (found "bat0" with matching hash)
> [IMG4TOOL] checking hash for "BatteryLow1"             OK (found "bat1" with matching hash)
> [IMG4TOOL] checking hash for "BatteryPlugin"           OK (found "glyP" with matching hash)
> [IMG4TOOL] checking hash for "Cryptex1,AppOS"          IGN (hash not found in im4m, but ignoring since not explicitly enforced through "Trusted"="YES" tag)
> [IMG4TOOL] checking hash for "Cryptex1,AppTrustCache"  IGN (hash not found in im4m, but ignoring since not explicitly enforced through "Trusted"="YES" tag)
> [IMG4TOOL] checking hash for "Cryptex1,AppVolume"      IGN (hash not found in im4m, but ignoring since not explicitly enforced through "Trusted"="YES" tag)
> [IMG4TOOL] checking hash for "Cryptex1,SystemOS"       IGN (hash not found in im4m, but ignoring since not explicitly enforced through "Trusted"="YES" tag)
> [IMG4TOOL] checking hash for "Cryptex1,SystemTrustCache"IGN (hash not found in im4m, but ignoring since not explicitly enforced through "Trusted"="YES" tag)
> [IMG4TOOL] checking hash for "Cryptex1,SystemVolume"   IGN (hash not found in im4m, but ignoring since not explicitly enforced through "Trusted"="YES" tag)
> [IMG4TOOL] checking hash for "DeviceTree"              OK (found "dtre" with matching hash)
> [IMG4TOOL] checking hash for "ISP"                     OK (found "ispf" with matching hash)
> [IMG4TOOL] checking hash for "KernelCache"             OK (found "krnl" with matching hash)
> [IMG4TOOL] checking hash for "LLB"                     OK (found "illb" with matching hash)
> [IMG4TOOL] checking hash for "Liquid"                  OK (found "liqd" with matching hash)
> [IMG4TOOL] checking hash for "Multitouch"              OK (untrusted)
> [IMG4TOOL] checking hash for "OS"                      OK (found "rosi" with matching hash)
> [IMG4TOOL] checking hash for "RecoveryMode"            OK (found "recm" with matching hash)
> [IMG4TOOL] checking hash for "RestoreDeviceTree"       OK (found "rdtr" with matching hash)
> [IMG4TOOL] checking hash for "RestoreKernelCache"      OK (found "rkrn" with matching hash)
> [IMG4TOOL] checking hash for "RestoreLogo"             OK (found "rlgo" with matching hash)
> [IMG4TOOL] checking hash for "RestoreRamDisk"          OK (found "rdsk" with matching hash)
> [IMG4TOOL] checking hash for "RestoreSEP"              OK (found "rsep" with matching hash)
> [IMG4TOOL] checking hash for "RestoreTrustCache"       OK (found "rtsc" with matching hash)
> [IMG4TOOL] checking hash for "SE,UpdatePayload"        IGN (no digest in BuildManifest)
> [IMG4TOOL] checking hash for "SEP"                     OK (found "sepi" with matching hash)
> [IMG4TOOL] checking hash for "StaticTrustCache"        OK (found "trst" with matching hash)
> [IMG4TOOL] checking hash for "SystemVolume"            OK (found "isys" with matching hash)
> [IMG4TOOL] checking hash for "ftap"                    IGN (no digest in BuildManifest)
> [IMG4TOOL] checking hash for "ftsp"                    IGN (no digest in BuildManifest)
> [IMG4TOOL] checking hash for "iBEC"                    OK (found "ibec" with matching hash)
> [IMG4TOOL] checking hash for "iBSS"                    OK (found "ibss" with matching hash)
> [IMG4TOOL] checking hash for "iBoot"                   OK (found "ibot" with matching hash)
> [IMG4TOOL] checking hash for "rfta"                    IGN (no digest in BuildManifest)
> [IMG4TOOL] checking hash for "rfts"                    IGN (no digest in BuildManifest)
> Verified APTicket to be valid for this restore
> Variant: Customer Erase Install (IPSW)
> This restore will erase all device data.
> Using cached filesystem from '/mnt/zram1/future_restore/iphonee/iPhone_5.5_P3_16.6_20G75_Restore/096-57032-079.dmg'
> Extracting iBEC.d21.RELEASE.im4p (Firmware/dfu/iBEC.d21.RELEASE.im4p)...
> Personalizing IMG4 component iBEC...
> Sending iBEC (1100370 bytes)...
> Getting SepNonce in recovery mode... 5c 75 19 c6 2d 7e 20 92 3d bf f6 dc d6 45 17 da 6a 70 5b 79 
> Getting ApNonce in recovery mode... 27 32 5c 82 58 be 46 e6 9d 9e e5 7f a9 a8 fb c2 8b 87 3d f4 34 e5 e7 02 a8 b2 79 99 55 11 38 ae 
> Recovery Mode Environment:
> iBoot build-version=iBoot-8422.142.2
> iBoot build-style=RELEASE
> Sending RestoreLogo...
> Extracting applelogo@3x~iphone.im4p (Firmware/all_flash/applelogo@3x~iphone.im4p)...
> Personalizing IMG4 component RestoreLogo...
> Sending RestoreLogo (20568 bytes)...
> ramdisk-size=0x20000000
> Extracting 096-57758-079.dmg (096-57758-079.dmg)...
> Personalizing IMG4 component RestoreRamDisk...
> Sending RestoreRamDisk (106962102 bytes)...
> Extracting AppleAVE2FW_H10.im4p (Firmware/ave/AppleAVE2FW_H10.im4p)...
> Personalizing IMG4 component AVE...
> Sending AVE (1083651 bytes)...
> Extracting adc-nike-d21.im4p (Firmware/isp_bni/adc-nike-d21.im4p)...
> Personalizing IMG4 component ISP...
> Sending ISP (8953011 bytes)...
> Extracting 096-57758-079.dmg.trustcache (Firmware/096-57758-079.dmg.trustcache)...
> Personalizing IMG4 component RestoreTrustCache...
> Sending RestoreTrustCache (12890 bytes)...
> Extracting DeviceTree.d21ap.im4p (Firmware/all_flash/DeviceTree.d21ap.im4p)...
> Personalizing IMG4 component RestoreDeviceTree...
> Sending RestoreDeviceTree (41750 bytes)...
> Extracting sep-firmware.d21.RELEASE.im4p (Firmware/all_flash/sep-firmware.d21.RELEASE.im4p)...
> Personalizing IMG4 component RestoreSEP...
> Sending RestoreSEP (1502158 bytes)...
> Extracting kernelcache.release.iphone10 (kernelcache.release.iphone10)...
> Personalizing IMG4 component RestoreKernelCache...
> Sending RestoreKernelCache (18217951 bytes)...
> getting SEP ticket
> Trying to fetch new SHSH blob
> Sending TSS request attempt 1... response successfully received
> Received SHSH blobs
> About to restore device... 
> Connecting now...
> Connected to com.apple.mobile.restored, version 15
> Device ffffffffffffffffffffffffffffffff00000001 has successfully entered restore mode
> Hardware Information:
> BoardID: 4
> ChipID: 32789
> UniqueChipID: 4878275665063854
> ProductionMode: true
> Starting Reverse Proxy
> ReverseProxy[Ctrl]: (status=1) Ready
> Checkpoint 1621 complete with code 0
> Checkpoint 1540 complete with code 0
> Checkpoint 1679 complete with code 0
> Checkpoint 1544 complete with code 0
> About to send RootTicket...
> Sending RootTicket now...
> Done sending RootTicket
> Checkpoint 94489282059 complete with code 0
> Waiting for NAND (28)
> Checkpoint 1549 complete with code 0
> Updating NAND Firmware (58)
> Checkpoint 1550 complete with code 0
> Checkpoint 1551 complete with code 0
> Checkpoint 1628 complete with code 0
> Checkpoint 1552 complete with code 0
> Checkpoint 7813868777962997267 complete with code 0
> Checkpoint 1662 complete with code 0
> About to send NORData...
> Found firmware path Firmware/all_flash
> Getting firmware manifest from build identity
> Extracting LLB.d21.RELEASE.im4p (Firmware/all_flash/LLB.d21.RELEASE.im4p)...
> Personalizing IMG4 component LLB...
> Extracting applelogo@3x~iphone.im4p (Firmware/all_flash/applelogo@3x~iphone.im4p)...
> Personalizing IMG4 component AppleLogo...
> Extracting batterycharging0@3x~iphone.im4p (Firmware/all_flash/batterycharging0@3x~iphone.im4p)...
> Personalizing IMG4 component BatteryCharging0...
> Extracting batterycharging1@3x~iphone.im4p (Firmware/all_flash/batterycharging1@3x~iphone.im4p)...
> Personalizing IMG4 component BatteryCharging1...
> Extracting batteryfull@3x~iphone.im4p (Firmware/all_flash/batteryfull@3x~iphone.im4p)...
> Personalizing IMG4 component BatteryFull...
> Extracting batterylow0@3x~iphone.im4p (Firmware/all_flash/batterylow0@3x~iphone.im4p)...
> Personalizing IMG4 component BatteryLow0...
> Extracting batterylow1@3x~iphone.im4p (Firmware/all_flash/batterylow1@3x~iphone.im4p)...
> Personalizing IMG4 component BatteryLow1...
> Extracting glyphplugin@1920~iphone-lightning.im4p (Firmware/all_flash/glyphplugin@1920~iphone-lightning.im4p)...
> Personalizing IMG4 component BatteryPlugin...
> Extracting DeviceTree.d21ap.im4p (Firmware/all_flash/DeviceTree.d21ap.im4p)...
> Personalizing IMG4 component DeviceTree...
> Extracting liquiddetect@1920~iphone-lightning.im4p (Firmware/all_flash/liquiddetect@1920~iphone-lightning.im4p)...
> Personalizing IMG4 component Liquid...
> Extracting recoverymode@1920~iphone-lightning.im4p (Firmware/all_flash/recoverymode@1920~iphone-lightning.im4p)...
> Personalizing IMG4 component RecoveryMode...
> Extracting iBoot.d21.RELEASE.im4p (Firmware/all_flash/iBoot.d21.RELEASE.im4p)...
> Personalizing IMG4 component iBoot...
> Personalizing IMG4 component RestoreSEP...
> Personalizing IMG4 component SEP...
> Sending NORData now...
> Done sending NORData
> Checkpoint 1545 complete with code 0
> Checkpoint 4294968979 complete with code 0
> Unmounting filesystems (29)
> Unmounting filesystems (29)
> Unmounting filesystems (29)
> Unmounting filesystems (29)
> Unmounting filesystems (29)
> Unmounting filesystems (29)
> Checkpoint 1637 complete with code 0
> Checkpoint 1556 complete with code 0
> Checkpoint 1686 complete with code 0
> Checkpoint 1699 complete with code 0
> Checkpoint 1695 complete with code 0
> Checkpoint 1620 complete with code 0
> Checkpoint 4294968983 complete with code 0
> Checkpoint 4294968853 complete with code 0
> About to send FDR Trust data...
> Sending FDR Trust data now...
> Done sending FDR Trust Data
> Checkpoint 4294968854 complete with code 0
> Checkpoint 1559 complete with code 0
> Checkpoint 38654707224 complete with code 0
> Checking for uncollected logs (44)
> Checkpoint 1561 complete with code 0
> Checking for uncollected logs (44)
> Checkpoint 1562 complete with code 0
> ReverseProxy[Ctrl]: (status=3) Connect Request
> ReverseProxy[Conn]: (status=1) Ready
> ReverseProxy[Conn]: (status=2) Terminated
> Checkpoint 1563 complete with code 0
> Checkpoint 1633 complete with code 0
> Unmounting filesystems (29)
> Unmounting filesystems (29)
> Unmounting filesystems (29)
> Unmounting filesystems (29)
> Unmounting filesystems (29)
> Unmounting filesystems (29)
> Checkpoint 9223372032559810077 complete with code 0
> Checkpoint 1614 complete with code 0
> Checkpoint 1567 complete with code 0
> Checkpoint 38654707338 complete with code 0
> Creating partition map (11)
> Checkpoint 1569 complete with code 0
> Checkpoint 1632 complete with code 0
> Checkpoint 1570 complete with code 0
> Checkpoint 1629 complete with code 0
> Checkpoint 5645 complete with code 0
> Creating filesystem (12)
> Checkpoint 1624 complete with code 0
> Checkpoint 1625 complete with code 0
> Checkpoint 1626 complete with code 0
> About to send filesystem...
> Connected to ASR
> Validating the filesystem
> Filesystem validated
> Sending filesystem now...
> [==================================================] 100.0%
> Done sending filesystem
> Verifying restore (14)
> [==================================================] 100.0%
> Checkpoint 1627 complete with code 0
> Checkpoint 1664 complete with code 0
> Checkpoint 1653 complete with code 0
> Checkpoint 1676 complete with code 0
> Unmounting filesystems (29)
> Unmounting filesystems (29)
> Unmounting filesystems (29)
> Unmounting filesystems (29)
> Unmounting filesystems (29)
> Unmounting filesystems (29)
> Checking filesystems (15)
> Checking filesystems (15)
> Checking filesystems (15)
> Mounting filesystems (16)
> Mounting filesystems (16)
> Mounting filesystems (16)
> Mounting filesystems (16)
> Checkpoint 1574 complete with code 0
> Checkpoint 1634 complete with code 0
> Checkpoint 1655 complete with code 0
> Checkpoint 1658 complete with code 0
> Checkpoint 30064772647 complete with code 0
> Checkpoint 38654707354 complete with code 0
> Checkpoint 1691 complete with code 0
> Checkpoint 1618 complete with code 0
> About to send BuildIdentity Dict...
> /tmp/Builder/repos/futurerestore/external/idevicerestore/src/common.c:supressed printing 94526 bytes plist...
> Sending BuildIdentityDict now...
> Done sending BuildIdentityDict
> Sending Cryptex1 TSS request...
> Sending TSS request attempt 1... response successfully received
> Received Cryptex1,Ticket
> Sending FirmwareResponse data now...
> Done sending FirmwareUpdater data
> About to send Cryptex1,SystemOS...
> Sending Cryptex1,SystemOS now...
> Done sending Cryptex1,SystemOS
> About to send Cryptex1,SystemVolume...
> Sending Cryptex1,SystemVolume now...
> Done sending Cryptex1,SystemVolume
> About to send Cryptex1,SystemTrustCache...
> Sending Cryptex1,SystemTrustCache now...
> Done sending Cryptex1,SystemTrustCache
> About to send Cryptex1,AppOS...
> Sending Cryptex1,AppOS now...
> Done sending Cryptex1,AppOS
> About to send Cryptex1,AppVolume...
> Sending Cryptex1,AppVolume now...
> Done sending Cryptex1,AppVolume
> About to send Cryptex1,AppTrustCache...
> Sending Cryptex1,AppTrustCache now...
> Done sending Cryptex1,AppTrustCache
> Checkpoint 1702 complete with code 0
> Checkpoint 1712 complete with code 0
> Checkpoint 4294968884 complete with code 0
> Checkpoint 30064772733 complete with code 0
> Unknown operation (80)
> Sending IsiBootEANFirmware image list
> Sending IsiBootNonEssentialFirmware image list
> Flashing firmware (18)
> [==================================================] 100.0%
> Checkpoint 4864 complete with code 0
> Unknown operation (80)
> Sending IsEarlyAccessFirmware image list
> Sending IsiBootEANFirmware image list
> Sending IsiBootNonEssentialFirmware image list
> Checkpoint 4294972178 complete with code 0
> Requesting FUD data (36)
> Found IsFUDFirmware component AOP
> Found IsFUDFirmware component AVE
> Found IsFUDFirmware component Ap,SystemVolumeCanonicalMetadata
> Found IsFUDFirmware component AudioCodecFirmware
> Found IsFUDFirmware component ISP
> Found IsFUDFirmware component Multitouch
> Found IsFUDFirmware component RestoreTrustCache
> Found IsFUDFirmware component StaticTrustCache
> Found IsFUDFirmware component SystemVolume
> Sending IsFUDFirmware image list
> Extracting aopfw-iphone10aop.im4p (Firmware/AOP/aopfw-iphone10aop.im4p)...
> Personalizing IMG4 component AOP...
> Sending IsFUDFirmware for AOP...
> Extracting AppleAVE2FW_H10.im4p (Firmware/ave/AppleAVE2FW_H10.im4p)...
> Personalizing IMG4 component AVE...
> Sending IsFUDFirmware for AVE...
> Extracting 096-57032-079.dmg.mtree (Firmware/096-57032-079.dmg.mtree)...
> Personalizing IMG4 component Ap,SystemVolumeCanonicalMetadata...
> Sending IsFUDFirmware for Ap,SystemVolumeCanonicalMetadata...
> Extracting D21_CallanFirmware.im4p (Firmware/D21_CallanFirmware.im4p)...
> Personalizing IMG4 component AudioCodecFirmware...
> Sending IsFUDFirmware for AudioCodecFirmware...
> Extracting adc-nike-d21.im4p (Firmware/isp_bni/adc-nike-d21.im4p)...
> Personalizing IMG4 component ISP...
> Sending IsFUDFirmware for ISP...
> Extracting D21_Multitouch.im4p (Firmware/D21_Multitouch.im4p)...
> Personalizing IMG4 component Multitouch...
> Sending IsFUDFirmware for Multitouch...
> Extracting 096-57758-079.dmg.trustcache (Firmware/096-57758-079.dmg.trustcache)...
> Personalizing IMG4 component RestoreTrustCache...
> Sending IsFUDFirmware for RestoreTrustCache...
> Extracting 096-57032-079.dmg.trustcache (Firmware/096-57032-079.dmg.trustcache)...
> Personalizing IMG4 component StaticTrustCache...
> Sending IsFUDFirmware for StaticTrustCache...
> Extracting 096-57032-079.dmg.root_hash (Firmware/096-57032-079.dmg.root_hash)...
> Personalizing IMG4 component SystemVolume...
> Sending IsFUDFirmware for SystemVolume...
> Checkpoint 4294972170 complete with code 0
> Updating gas gauge software (47)
> Checkpoint 38654710529 complete with code 0
> Updating gas gauge software (47)
> Checkpoint 4866 complete with code 0
> Updating Stockholm (55)
> Checkpoint 38654710532 complete with code 0
> Requesting FUD data (36)
> Found IsFUDFirmware component AOP
> Found IsFUDFirmware component AVE
> Found IsFUDFirmware component Ap,SystemVolumeCanonicalMetadata
> Found IsFUDFirmware component AudioCodecFirmware
> Found IsFUDFirmware component ISP
> Found IsFUDFirmware component Multitouch
> Found IsFUDFirmware component RestoreTrustCache
> Found IsFUDFirmware component StaticTrustCache
> Found IsFUDFirmware component SystemVolume
> Sending IsFUDFirmware image list
> Extracting 096-57032-079.dmg.mtree (Firmware/096-57032-079.dmg.mtree)...
> Personalizing IMG4 component Ap,SystemVolumeCanonicalMetadata...
> Sending IsFUDFirmware for Ap,SystemVolumeCanonicalMetadata...
> Extracting 096-57758-079.dmg.trustcache (Firmware/096-57758-079.dmg.trustcache)...
> Personalizing IMG4 component RestoreTrustCache...
> Sending IsFUDFirmware for RestoreTrustCache...
> Extracting 096-57032-079.dmg.root_hash (Firmware/096-57032-079.dmg.root_hash)...
> Personalizing IMG4 component SystemVolume...
> Sending IsFUDFirmware for SystemVolume...
> Checkpoint 4876 complete with code 0
> Checkpoint 4870 complete with code 0
> Checkpoint 4871 complete with code 0
> Checkpoint 4872 complete with code 0
> Checkpoint 38654710542 complete with code 0
> Checkpoint 3910304540297007887 complete with code 0
> Checkpoint 38654710539 complete with code 0
> Updating baseband (19)
> About to send BasebandData...
> sending request without baseband nonce
> Sending Baseband TSS request...
> Sending TSS request attempt 1... response successfully received
> Received Baseband SHSH blobs
> Sending BasebandData now...
> Done sending BasebandData
> Updating Baseband in progress...
> About to send BasebandData...
> Sending Baseband TSS request...
> Sending TSS request attempt 1... response successfully received
> Received Baseband SHSH blobs
> Sending BasebandData now...
> Done sending BasebandData
> Updating Baseband completed.
> Checkpoint 4867 complete with code 0
> Checkpoint 38654710544 complete with code 0
> Checkpoint 3355045627762316063 complete with code 0
> Updating SE Firmware (59)
> About to send BuildIdentity Dict...
> /tmp/Builder/repos/futurerestore/external/idevicerestore/src/common.c:supressed printing 94526 bytes plist...
> Sending BuildIdentityDict now...
> Done sending BuildIdentityDict
> About to send SE,UpdatePayload...
> Sending SE,UpdatePayload now...
> Done sending SE,UpdatePayload
> Extracting Stockholm4.RELEASE.sefw (Firmware/SE/Stockholm4.RELEASE.sefw)...
> Sending SE TSS request...
> Sending TSS request attempt 1... response successfully received
> Received SE,Ticket
> Extracting Stockholm4.RELEASE.sefw (Firmware/SE/Stockholm4.RELEASE.sefw)...
> Sending FirmwareResponse data now...
> Done sending FirmwareUpdater data
> Checkpoint 3355045627762316041 complete with code 0
> Checkpoint 38654710541 complete with code 0
> Updating Veridian (66)
> Checkpoint 38654710545 complete with code 0
> Unknown operation (79)
> Checkpoint 4886 complete with code 0
> Unknown operation (82)
> Checkpoint 4888 complete with code 0
> Unknown operation (85)
> Checkpoint 4896 complete with code 0
> Checkpoint 1589 complete with code 0
> Checkpoint 4294968892 complete with code 0
> Checkpoint 5377 complete with code 0
> Checkpoint 5376 complete with code 0
> Checkpoint 5379 complete with code 0
> Checkpoint 4890 complete with code 0
> Checkpoint 4889 complete with code 0
> Requesting EAN Data (74)
> Checkpoint 5380 complete with code 0
> Checkpoint 4884 complete with code 0
> Checkpoint 38654707304 complete with code 0
> Checkpoint 1597 complete with code 0
> Checkpoint 38654707311 complete with code 0
> Creating Protected Volume (67)
> Checkpoint 13388932732730476148 complete with code 0
> Checkpoint 30064772703 complete with code 0
> About to send KernelCache...
> Extracting kernelcache.release.iphone10 (kernelcache.release.iphone10)...
> Personalizing IMG4 component KernelCache...
> Sending KernelCache now...
> Done sending KernelCache
> Installing kernelcache (27)
> Checkpoint 3584 complete with code 0
> About to send DeviceTree...
> Extracting DeviceTree.d21ap.im4p (Firmware/all_flash/DeviceTree.d21ap.im4p)...
> Personalizing IMG4 component DeviceTree...
> Sending DeviceTree now...
> Done sending DeviceTree
> Installing DeviceTree (61)
> Checkpoint 3585 complete with code 0
> About to send NORData...
> Found firmware path Firmware/all_flash
> Getting firmware manifest from build identity
> Extracting LLB.d21.RELEASE.im4p (Firmware/all_flash/LLB.d21.RELEASE.im4p)...
> Personalizing IMG4 component LLB...
> Extracting applelogo@3x~iphone.im4p (Firmware/all_flash/applelogo@3x~iphone.im4p)...
> Personalizing IMG4 component AppleLogo...
> Extracting batterycharging0@3x~iphone.im4p (Firmware/all_flash/batterycharging0@3x~iphone.im4p)...
> Personalizing IMG4 component BatteryCharging0...
> Extracting batterycharging1@3x~iphone.im4p (Firmware/all_flash/batterycharging1@3x~iphone.im4p)...
> Personalizing IMG4 component BatteryCharging1...
> Extracting batteryfull@3x~iphone.im4p (Firmware/all_flash/batteryfull@3x~iphone.im4p)...
> Personalizing IMG4 component BatteryFull...
> Extracting batterylow0@3x~iphone.im4p (Firmware/all_flash/batterylow0@3x~iphone.im4p)...
> Personalizing IMG4 component BatteryLow0...
> Extracting batterylow1@3x~iphone.im4p (Firmware/all_flash/batterylow1@3x~iphone.im4p)...
> Personalizing IMG4 component BatteryLow1...
> Extracting glyphplugin@1920~iphone-lightning.im4p (Firmware/all_flash/glyphplugin@1920~iphone-lightning.im4p)...
> Personalizing IMG4 component BatteryPlugin...
> Extracting DeviceTree.d21ap.im4p (Firmware/all_flash/DeviceTree.d21ap.im4p)...
> Personalizing IMG4 component DeviceTree...
> Extracting liquiddetect@1920~iphone-lightning.im4p (Firmware/all_flash/liquiddetect@1920~iphone-lightning.im4p)...
> Personalizing IMG4 component Liquid...
> Extracting recoverymode@1920~iphone-lightning.im4p (Firmware/all_flash/recoverymode@1920~iphone-lightning.im4p)...
> Personalizing IMG4 component RecoveryMode...
> Extracting iBoot.d21.RELEASE.im4p (Firmware/all_flash/iBoot.d21.RELEASE.im4p)...
> Personalizing IMG4 component iBoot...
> Personalizing IMG4 component RestoreSEP...
> Personalizing IMG4 component SEP...
> Sending NORData now...
> Done sending NORData
> Checkpoint 3588 complete with code 0
> About to send SystemImageRootHash...
> Extracting 096-57032-079.dmg.root_hash (Firmware/096-57032-079.dmg.root_hash)...
> Personalizing IMG4 component SystemVolume...
> Sending SystemImageRootHash now...
> Done sending SystemImageRootHash
> Checkpoint 3589 complete with code 0
> Checkpoint 3587 complete with code 0
> Checkpoint 30064772648 complete with code 0
> Fixing up /var (17)
> Creating system key bag (50)
> Checkpoint 3840 complete with code 0
> Checkpoint 3841 complete with code 0
> Checkpoint 3844 complete with code 0
> ReverseProxy[Ctrl]: (status=3) Connect Request
> ReverseProxy[Conn]: (status=1) Ready
> Checkpoint 3849 complete with code 0
> Checkpoint 30064772685 complete with code 0
> ReverseProxy[Conn]: (status=2) Terminated
> Checkpoint 1694 complete with code 0
> Checkpoint 3355045627762312763 complete with code 0
> Modifying persistent boot-args (25)
> Checkpoint 1593 complete with code 0
> Checkpoint 3355045627762312798 complete with code 0
> Checkpoint 1672 complete with code 0
> Checkpoint 4294968955 complete with code 0
> Checkpoint 18446744069414589952 complete with code 0
> Checkpoint 1599 complete with code 0
> Checkpoint 38654707264 complete with code 0
> About to send SystemImageCanonicalMetadata...
> Extracting 096-57032-079.dmg.mtree (Firmware/096-57032-079.dmg.mtree)...
> Personalizing IMG4 component Ap,SystemVolumeCanonicalMetadata...
> Sending SystemImageCanonicalMetadata now...
> Done sending SystemImageCanonicalMetadata
> About to send SystemImageRootHash...
> Extracting 096-57032-079.dmg.root_hash (Firmware/096-57032-079.dmg.root_hash)...
> Personalizing IMG4 component SystemVolume...
> Sending SystemImageRootHash now...
> Done sending SystemImageRootHash
> Unmounting filesystems (29)
> Unmounting filesystems (29)
> Unmounting filesystems (29)
> Unmounting filesystems (29)
> Unmounting filesystems (29)
> Unmounting filesystems (29)
> Sealing System Volume (77)
> Checkpoint 1675 complete with code 0
> Checkpoint 4294968937 complete with code 0
> Checkpoint 38654707370 complete with code 0
> Checkpoint 1707 complete with code 0
> Unmounting filesystems (29)
> Unmounting filesystems (29)
> Unmounting filesystems (29)
> Unmounting filesystems (29)
> Unmounting filesystems (29)
> Unmounting filesystems (29)
> Checkpoint 1602 complete with code 0
> Checkpoint 1660 complete with code 0
> Checkpoint 5651 complete with code 0
> Checkpoint 1607 complete with code 0
> Got status message
> Status: Restore Finished
> ReverseProxy[Ctrl]: (status=2) Terminated
> Cleaning up...
> Done: restoring succeeded!
> future_restore/iphonee ❯                                                                                                                            took 7m31s 
> ```
# Disk swap

A swap partition or file provides storage for anonymous memory pages that the kernel chooses to reclaim. Swap can be used before RAM is entirely full, and adding it does not guarantee that a system under memory pressure will avoid an out-of-memory condition. Its effect depends on the workload and storage.

A persistent swap area can also be used for hibernation when it is large enough and early resume is configured correctly. Zram cannot itself preserve a hibernation image across power loss. See [ArchWiki: hibernation](https://wiki.archlinux.org/title/Power_management/Suspend_and_hibernate#Hibernation).

Zram swap and disk swap can coexist. Swap priorities influence which area the kernel uses first; choose and test priorities for your workload. See [[ZRAM Setup]], [[Creating Swap Partition (During Arch Install)]], and [[Tuning Swap Performance (Advanced)]].


```bash
paru -S preload
```

```bash
sudo nvim /etc/preload.conf
```

> [!NOTE]- defaut config
> ```ini
> [model]
> 
> # cycle:
> #
> # This is the quantum of time for preload.  Preload performs
> # data gathering and predictions every cycle.  Use an even
> # number.
> #
> # Note: Setting this parameter too low may reduce system performance
> # and stability.
> #
> # unit: seconds
> # default: 20
> #
> cycle = 20
> 
> # usecorrelation:
> #
> # Whether correlation coefficient should be used in the prediction
> # algorithm.  There are arguments both for and against using it.
> # Currently it's believed that using it results in more accurate
> # prediction.  The option may be removed in the future.
> #
> # default: true
> usecorrelation = true
> 
> # minsize:
> #
> # Minimum sum of the length of maps of the process for
> # preload to consider tracking the application.
> #
> # Note: Setting this parameter too high will make preload less
> # effective, while setting it too low will make it eat
> # quadratically more resources, as it tracks more processes.
> #
> # unit: bytes
> # default: 2000000
> #
> minsize = 2000000
> 
> #
> # The following control how much memory preload is allowed to use
> # for preloading in each cycle.  All values are percentages and are
> # clamped to -100 to 100.
> #
> # The total memory preload uses for prefetching is then computed using
> # the following formulae:
> #
> # 	max (0, TOTAL * memtotal + FREE * memfree) + CACHED * memcached
> # where TOTAL, FREE, and CACHED are the respective values read at
> # runtime from /proc/meminfo.
> #
> 
> # memtotal: precentage of total memory
> #
> # unit: signed_integer_percent
> # default: -10
> #
> memtotal = -10
> 
> # memfree: precentage of free memory
> #
> # unit: signed_integer_percent
> # default: 50
> #
> memfree = 50
> 
> # memcached: precentage of cached memory
> #
> # unit: signed_integer_percent
> # default: 0
> #
> memcached = 0
> 
> 
> ###########################################################################
> 
> [system]
> 
> # doscan:
> #
> # Whether preload should monitor running processes and update its
> # model state.  Normally you do want that, that's all preload is
> # about, but you may want to temporarily turn it off for various
> # reasons like testing and only make predictions.  Note that if
> # scanning is off, predictions are made based on whatever processes
> # have been running when preload started and the list of running
> # processes is not updated at all.
> #
> # default: true
> doscan = true
> 
> # dopredict:
> #
> # Whether preload should make prediction and prefetch anything off
> # the disk.  Quite like doscan, you normally want that, that's the
> # other half of what preload is about, but you may want to temporarily
> # turn it off, to only train the model for example.  Note that
> # this allows you to turn scan/predict or or off on the fly, by
> # modifying the config file and signalling the daemon.
> #
> # default: true
> dopredict = true
> 
> # autosave:
> #
> # Preload will automatically save the state to disk every
> # autosave period.  This is only relevant if doscan is set to true.
> # Note that some janitory work on the model, like removing entries
> # for files that no longer exist happen at state save time.  So,
> # turning off autosave completely is not advised.
> #
> # unit: seconds
> # default: 3600
> #
> autosave = 3600
> 
> # mapprefix:
> #
> # A list of path prefixes that controll which mapped file are to
> # be considered by preload and which not.  The list items are
> # separated by semicolons.  Matching will be stopped as soon as
> # the first item is matched.  For each item, if item appears at
> # the beginning of the path of the file, then a match occurs, and
> # the file is accepted.  If on the other hand, the item has a
> # exclamation mark as its first character, then the rest of the
> # item is considered, and if a match happens, the file is rejected.
> # For example a value of !/lib/modules;/ means that every file other
> # than those in /lib/modules should be accepted.  In this case, the
> # trailing item can be removed, since if no match occurs, the file is
> # accepted.  It's advised to make sure /dev is rejected, since
> # preload doesn't special-handle device files internally.
> #
> # Note that /lib matches all of /lib, /lib64, and even /libexec if
> # there was one.  If one really meant /lib only, they should use
> # /lib/ instead.
> #
> # default: (empty list, accept all)
> mapprefix = /opt;/usr/;/lib;/var/cache/;!/
> 
> # exeprefix:
> #
> # The syntax for this is exactly the same as for mapprefix.  The only
> # difference is that this is used to accept or reject binary exectuable
> # files instead of maps.
> #
> # default: (empty list, accept all)
> exeprefix = /opt;!/usr/sbin/;!/usr/local/sbin/;/usr/;!/
> 
> # maxprocs
> #
> # Maximum number of processes to use to do parallel readahead.  If
> # equal to 0, no parallel processing is done and all readahead is
> # done in-process.  Parallel readahead supposedly gives a better I/O
> # performance as it allows the kernel to batch several I/O requests
> # of nearby blocks.
> #
> # default: 30
> processes = 30
> 
> # sortstrategy
> #
> # The I/O sorting strategy.  Ideally this should be automatically
> # decided, but it's not currently.  One of:
> #
> #   0 -- SORT_NONE:	No I/O sorting.
> #			Useful on Flash memory for example.
> #   1 -- SORT_PATH:	Sort based on file path only.
> #			Useful for network filesystems.
> #   2 -- SORT_INODE:	Sort based on inode number.
> #			Does less house-keeping I/O than the next option.
> #   3 -- SORT_BLOCK:	Sort I/O based on disk block.  Most sophisticated.
> #			And useful for most Linux filesystems.
> #
> # default: 3
> sortstrategy = 3
> ```

> [!NOTE]- Optimized for 64 GB (Aggressive)
> ```ini
> [model]
> 
> # cycle:
> #
> # This is the quantum of time for preload.  Preload performs
> # data gathering and predictions every cycle.  Use an even
> # number.
> #
> # Note: Setting this parameter too low may reduce system performance
> # and stability.
> #
> # unit: seconds
> # default: 20
> #
> cycle = 10
> 
> # usecorrelation:
> #
> # Whether correlation coefficient should be used in the prediction
> # algorithm.  There are arguments both for and against using it.
> # Currently it's believed that using it results in more accurate
> # prediction.  The option may be removed in the future.
> #
> # default: true
> usecorrelation = true
> 
> # minsize:
> #
> # Minimum sum of the length of maps of the process for
> # preload to consider tracking the application.
> #
> # Note: Setting this parameter too high will make preload less
> # effective, while setting it too low will make it eat
> # quadratically more resources, as it tracks more processes.
> #
> # unit: bytes
> # default: 2000000
> #
> minsize = 500000
> 
> #
> # The following control how much memory preload is allowed to use
> # for preloading in each cycle.  All values are percentages and are
> # clamped to -100 to 100.
> #
> # The total memory preload uses for prefetching is then computed using
> # the following formulae:
> #
> # 	max (0, TOTAL * memtotal + FREE * memfree) + CACHED * memcached
> # where TOTAL, FREE, and CACHED are the respective values read at
> # runtime from /proc/meminfo.
> #
> 
> # memtotal: precentage of total memory
> #
> # unit: signed_integer_percent
> # default: -10
> #
> memtotal = 50
> 
> # memfree: precentage of free memory
> #
> # unit: signed_integer_percent
> # default: 50
> #
> memfree = 95
> 
> # memcached: precentage of cached memory
> #
> # unit: signed_integer_percent
> # default: 0
> #
> memcached = 10
> 
> 
> ###########################################################################
> 
> [system]
> 
> # doscan:
> #
> # Whether preload should monitor running processes and update its
> # model state.  Normally you do want that, that's all preload is
> # about, but you may want to temporarily turn it off for various
> # reasons like testing and only make predictions.  Note that if
> # scanning is off, predictions are made based on whatever processes
> # have been running when preload started and the list of running
> # processes is not updated at all.
> #
> # default: true
> doscan = true
> 
> # dopredict:
> #
> # Whether preload should make prediction and prefetch anything off
> # the disk.  Quite like doscan, you normally want that, that's the
> # other half of what preload is about, but you may want to temporarily
> # turn it off, to only train the model for example.  Note that
> # this allows you to turn scan/predict or or off on the fly, by
> # modifying the config file and signalling the daemon.
> #
> # default: true
> dopredict = true
> 
> # autosave:
> #
> # Preload will automatically save the state to disk every
> # autosave period.  This is only relevant if doscan is set to true.
> # Note that some janitory work on the model, like removing entries
> # for files that no longer exist happen at state save time.  So,
> # turning off autosave completely is not advised.
> #
> # unit: seconds
> # default: 3600
> #
> autosave = 3600
> 
> # mapprefix:
> #
> # A list of path prefixes that controll which mapped file are to
> # be considered by preload and which not.  The list items are
> # separated by semicolons.  Matching will be stopped as soon as
> # the first item is matched.  For each item, if item appears at
> # the beginning of the path of the file, then a match occurs, and
> # the file is accepted.  If on the other hand, the item has a
> # exclamation mark as its first character, then the rest of the
> # item is considered, and if a match happens, the file is rejected.
> # For example a value of !/lib/modules;/ means that every file other
> # than those in /lib/modules should be accepted.  In this case, the
> # trailing item can be removed, since if no match occurs, the file is
> # accepted.  It's advised to make sure /dev is rejected, since
> # preload doesn't special-handle device files internally.
> #
> # Note that /lib matches all of /lib, /lib64, and even /libexec if
> # there was one.  If one really meant /lib only, they should use
> # /lib/ instead.
> #
> # default: (empty list, accept all)
> # Added /usr/local/ for self-compiled tools common in Arch
> mapprefix = /opt;/usr/;/lib;/var/cache/;!/
> # exeprefix:
> #
> # The syntax for this is exactly the same as for mapprefix.  The only
> # difference is that this is used to accept or reject binary exectuable
> # files instead of maps.
> #
> # default: (empty list, accept all)
> exeprefix = /opt;!/usr/sbin/;!/usr/local/sbin/;!/usr/local/;/usr/;/lib;!/
> # maxprocs
> #
> # Maximum number of processes to use to do parallel readahead.  If
> # equal to 0, no parallel processing is done and all readahead is
> # done in-process.  Parallel readahead supposedly gives a better I/O
> # performance as it allows the kernel to batch several I/O requests
> # of nearby blocks.
> #
> # default: 30
> processes = 60
> 
> # sortstrategy
> #
> # The I/O sorting strategy.  Ideally this should be automatically
> # decided, but it's not currently.  One of:
> #
> #   0 -- SORT_NONE:	No I/O sorting.
> #			Useful on Flash memory for example.
> #   1 -- SORT_PATH:	Sort based on file path only.
> #			Useful for network filesystems.
> #   2 -- SORT_INODE:	Sort based on inode number.
> #			Does less house-keeping I/O than the next option.
> #   3 -- SORT_BLOCK:	Sort I/O based on disk block.  Most sophisticated.
> #			And useful for most Linux filesystems.
> #
> # default: 3
> sortstrategy = 0
> ```

```bash
sudo systemctl enable --now preload
```
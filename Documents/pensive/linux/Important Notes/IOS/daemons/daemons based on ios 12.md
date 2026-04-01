|+|A|B|
|---|---|---|
|1|ABDatabaseDoctor|AddressBook database repair|
|2|absd|Application identifier Fairplay Client Connected #DRM plugin?|
|3|accessoryd|Removing this will make accessories such as docks and other cables not be able to do anything except charge.|
|4|accountsd|Accountsd is a daemon, part of the Accounts framework. Apple's developer documentation says this framework helps users access and manage their external accounts from within apps, without requiring them to enter login credentials|
|5|adid|??? Ad id?|
|6|AdminLite|When and app is not responding, this force closes it. You'll have to wait for unresponsive apps if you delete this. The AdminLite framework's sole purpose is act as a client to the com.apple.AdminLite service. There is are 2 functions in this framework, the high-level AdminLiteNVRAMSet and the low-level nvram_set.|
|7|afcd|AFC (Apple File Conduit) is a service that runs on every iPhone / iPod, which iTunes uses to exchange files with the device. It is jailed to the directory /private/var/mobile/Media, which is on the second (non-OS) partition. The AFC service is handled by /usr/libexec/afcd, and runs over the usbmux protocol.|
|8|aggregate|Create aggregate logs|
|9|akd|AuthKit|
|10|and|keyboardlayout|
|11|Applecredentialmanager|?|
|12|ApplecredentialManager||
|13|apsd|apple push service|
|14|askpermissiond|It is a security/safety process that detects if the user should be asked for permission by password or other notification.|
|15|aslmanager|apple system log|
|16|assertion_agent|Create power assertion to prevent different kinds of sleep|
|17|assertiond|Assertiond is the iOS system daemon responsible for monitoring application performance and access rights at runtime. Two things it monitors are the wall-clock time and the CPU time a process has access to.|
|18|AssetCacheLocator|The caching server speeds up the download of software distributed by Apple through the Internet.|
|19|assetsd|connected to the photos application "doesnt work without it". Assetsd "handles names and description of photos" I think|
|20|assistivetouchd|assistivetouchd "a funtional shortcutbutton that is draggable anywhere on the homescreen"|
|21|atc|air traffic control|
|22|atc.atwakeup|ATWAKEUP daemon sends a ping (aka signal) approximately every 10 seconds to any "sleeping" paired Bluetooth media device to wake it up...just in case you press the "Play" button in either the control center or multitasking view.|
|23|auth.agent||
|24|AuthBrokerAgent|The AuthBrokerAgent is responsible for handling proxy credentials. If the credentials of a proxy setup are improperly stored (for example in one's keychain) then the AuthBrokerAgent can runaway.|
|25|awdd|apple wireless diagnostics daemon|
|26|backboardd|BackBoard, is a daemon introduced in iOS 6 to take some of the workload off of SpringBoard. Its chief purpose is to handle events from the hardware, such as touches, button presses, and accelerometer information.|
|27|backupd|backupd daemon backs up your files every hour, meaning that when your Time Machine backup is running, you'll notice backupd using up some CPU and memory|
|28|bird|icloud documents|
|29|Bluetool|???? probably bluetooth related no sure try to keep off|
|30|bluetoothd|bluetooth module|
|31|bookassetd|seems to be tied to the download of books with the app "books"|
|32|bootps|my own plugin probably|
|33|BTServer|Bluetooth|
|34|BTServer.le|Bluetooth|
|35|BTServer.map|Bluetooth|
|36|BTServer.pbap|Bluetooth|
|37|cache_delete|deletes some kind of cache|
|38|cache_delete_app|deletes some kind of cache|
|39|cache_delete_daily|deletes some kind of cache|
|40|cache_delete_mobile|deletes some kind of cache|
|41|calaccessd|runs when it syncs with email accounts or anything to do with your calender|
|42|CallHistorySyncHelper|for mobile phones|
|43|captiveagent|Handle captive WiFi login|
|44|cdpd|tied to account and login services "probably a securityservice"|
|45|certui.relay|When you are on a public network (like my school) and Safari can't verify what website it is connecting to, it will say "This website is not verified" or something like that, and asks if you want to still continue. Feel free to delete this.|
|46|cfnetworkagent|Core Foundation networking|
|47|cfprefsd.xpc.daemon|Core Foundation preference sync|
|48|circlejoinrequested|?????? securityrelated / Has something to do with connecting to nearby iOS devices but i could be wrong.|
|49|cloudd|icloud|
|50|cloudkeychianproxy|iCloud Keychain|
|51|cloudpaird|icloud related service|
|52|cloudphotod|icloud related service|
|53|CloudPhotoDerivative|Thumbnails for icloud|
|54|cmfsyncagent|Communications Filter Synchronization Agent|
|55|CommCenter|cellular data network|
|56|CommCenterMobileHelper|cellular data network|
|57|CommCenterRootHelper|cellular data network|
|58|companion_proxy|handles connections to certain other devices using ports [https://docs.libimobiledevice.org/libimobiledevice/latest/companion__proxy_8h.html](https://docs.libimobiledevice.org/libimobiledevice/latest/companion__proxy_8h.html)|
|59|configd|System Configuration Server, which means it monitors and reports on your Mac’s settings and status|
|60|contacts donation agent|Provide contacts from a sync provider|
|61|contactsd|Contacts handler|
|62|containermanagerd|Mange App and Group containers|
|63|contextstored|coreduetrelated which means it monitors and reports on your Mac’s settings and status|
|64|CoreAuthentication|handles biometric data / touch id|
|65|corecaptured|WiFi / Bluetooth diagnostic capture|
|66|coreduetd|Coreduetd is the daemon that is used to coordinate with your Mac other Apple devices in close proximity, to facilitate Handoff and synchronize with Office 365, iCloud Drive, and similar.|
|67|coreidvd|iDVD is a discontinued DVD-creation application for Mac OS produced by Apple Inc. iDVD allows the user to burn QuickTime movies, MP3 music, ...Final release: 7.1.2 / July 11, 2011; this may be required for imovie|
|68|coreparsec.sillhouette|part of Siri AI|
|69|coreservices.lsactivity.plist|lsactivity controls and helps with testing of the UserActivity feature and frameworks. The tool processes its arguments as command [ options... ] [ command [options...]]*|
|70|corespotlightservice.plist|spotlight|
|71|coresymbolicationd.plist|25 Symbolication means replacing memory addresses with symbols (like functions or variables) or for example adding function names and line number information. It is used for debugging and analyzing crash reports.|
|72|cr4shedd|Crashed/ has to do with logging and diagnostics|
|73|crash_mover.plist|moves crash logs|
|74|CrashHousekeeping|removes logs|
|75|crashreportcopymobile.plist|copy crash log|
|76|ctkd.plist|Cloud Token Kit daemon|
|77|Cydia.startup||
|78|dasd.plist|Duet Activity Scheduler (DAS) maintains a scored list of background activities which usually consists of more than seventy items. Periodically, it rescores each item in its list, according to various criteria such as whether it is now due to be performed, i.e. clock time is now within the time period in which Centralized Task Scheduling (CTS) calculated it should next be run.|
|79|dataaccessd|his daemon deals with syncing with Exchange and Google Sync|
|80|DataDetectorsSource|The DataDetectorsSourceAccess command manages and controls access to the content of dynamic sources for the DataDetectors clients. This tool should not be run directly.|
|81|defragd|Defragment for APFS|
|82|destinationd|maps destination service|
|83|device-o-matic|Figure out what profile to present when the device is connected via USB|
|84|devicecheckd|DeviceCheck is anticheat/modify software, might be riquiredReduce fraudulent use of your services by managing device state and asserting app integrity.|
|85|diagnosticd|Diagnostics|
|86|diagnosticextensionsd|Diagnostic extension / plugins|
|87|distnoted|Distributed notificaions Distnoted is a system message notification process. Often Distnoted will go wild when another process crashes. This other process is what distnoted is attempting to processs the system message for. Locum is an example of a process that sometimes crashes and is tied to finder.|
|88|dmd|YNOPSIS remotemanagementd DESCRIPTION remotemanagementd handles HTTP communication with an Mobile Device Management (MDM) Version 2 server, delivering configuration information to the local Device Management daemon (dmd), and sending status messages back to the server.|
|89|DMHelper|Kechain, SSL, bla bla, keep on|
|90|DragUI|multitasking UI|
|91|duetexpertd|reportedly an iCloud-related service. Not removable.????|
|92|DumpBasebandCrash|Dumps crash for baseband. Baseband/telephony, disable if you do not want crash log or if you dont have mobile network|
|93|DumpPanic|Dumps a log|
|94|EscrowSecurityAlert|Escrow is a data security measure in which acryptographic key is entrusted to a third party (i.e., kept in escrow). Under normal circumstances, the key is not released to someone other than the sender or receiver without proper authorization|
|95|fairplayd.A2|FairPlay is Apple's propietary DRM used to encrypt media purchased from the iTunes Store|
|96|familycircled|familycircled is a daemon for iCloud Family|
|97|familynotificationd|notification about family control "kid lock"|
|98|fdrhelper|Factory Data Reset|
|99|Filecoordination|coordinates the reading and writing of files and directories among file presenters.|
|100|Fileprovider|An extension other apps use to access files and folders managed by your app and synced with a remote storage.|
|101|findmydeviced|find my iphone|
|102|fmfd|Find my friends|
|103|fmflocatord|Find my friends|
|104|followupd|This looks like it's involved in the mechanics of notifications on a low level|
|105|fs_task_scheduler|filesystem task scheduler|
|106|fseventsd|Filesystem events|
|107|ftp-proxy-embedded|File Transfer Protocol, keep this on|
|108|GameController||
|109|gamed|gamecenter|
|110|geocorrectiond|GPS/Telemetry related|
|111|geod|Geo-fencing + GPS|
|112|GSSCred|The gsscred table is used on server machines to lookup the uid of incoming clients connected using RPCSEC_GSS|
|113|hangreporter|logging of unactive apps "unactive apps are spinning, and will be sampled by spindump tool to dump logs"|
|114|hangtracerd|logger|
|115|healthd|HealthKit|
|116|heartbeat|WatchOS heartbeat?|
|117|homed|HomeKit|
|118|hostapd|user space daemon for access points, including, e.g., IEEE 802.1X/WPA/EAP Authenticator for number of Linux and BSD drivers|
|119|house_arreset|iTunes file transfer for application documents|
|120|iap2d|USB 2.0 Acessory ON/OFF|
|121|iapauthd|iAccessory authentication|
|122|iapd|iAccessory Protocol handler Deals with companion apps for accessories|
|123|iaptransportd|iAccessory|
|124|idamd|???????|
|125|identityservicesd|identityservicesd is a background process (Identity Services Daemon) that deals with third-party credentials|
|126|idscredentialsagent|identity services|
|127|idsremoteurlconnectionagent|That software is ready to make any Instant Messaging connections you decide to make.|
|128|imagent|Instant Message Agent (iMessage)|
|129|imautomatischistorydeletionagent|iMessage History Deletion Agent|
|130|imtransferagent|iMessage attachment handler|
|131|ind.plist|????????|
|132|insecure_notification|??????? #If i remember correctly. This daemon went absolutely crazy at some point. It can become a CPU hog.|
|133|installation_proxy|Manages applications on a device. [most likely needed]|
|134|installcoordinationd|installation software for installing apps|
|135|installd|Mandatory for Appstore, probably installation software|
|136|IOAccelMemoryInfoCollector|probably logging|
|137|iomfb_bics_daemon|maybe needed for banking apps and other apps|
|138|iosdiagnosticsd|iOS Diagnostics is an Apple internal application. It is the iOS equivalent of an internal Apple OS X application named "Behavior Scan", used at the Genius Bar to detect and test different aspects of the device. ... app, and the diagnostic data is sent over the air to the genius' device|
|139|lskdmsed|Local System Kernel Debug Memory Daemon?|
|140|itunescloudd|iTunes cloud and Home Sharing|
|141|itunesstored|Mandatory for Appstore|
|142|jetsam 12412526||
|143|keybagd|Keybagd is a service that first appeared on iOS, and was used to control access to encrypted user data based on device state- some data can still be unencrypted while the device remains locked, but other data cannot. It's since migrated into macOS.|
|144|languageassetd|may be related to Siri try to leave on|
|145|libnotificationd|DEBUGGING Disable used by cr4shed|
|146|locationd|Location services|
|147|lockdown|lockdownd is a daemon that provides system information to clients using liblockdown.dylib, e.g. the IMEI, UDID, etc. Every information provided by lockdownd can be obtained via other means, e.g. the IMEI can be found using IOKit. The only advantage of using lockdownd is it has root privilege, hence avoiding having to assume super user.|
|148|logd|Logging Daemon|
|149|lsd|Launch Services daemon|
|150|lskdd|Local System Kernel Debug Daemon?|
|151|managedconfiguration -mdm|Mobile Device Manager daemon|
|152|managedconfiguration -tesla||
|153|managedconfiguration-profile|.mobileprofile management|
|154|Maps.pushdaemon|push notification for maps?|
|155|matd|??? försök att ha på|
|156|mDNSResponer|Behövs för Webbsidor mDNSResponder, is a core part of the Bonjour protocol. Bonjour is Apple’s zero-configuration networking service, which basically means it’s how Apple devices find each other on a network. Our process, mDNSResponder, regularly scans your local network looking for other Bonjour-enabled devices.|
|157|MDNSResponerHelper|mDNSResponder, is a core part of the Bonjour protocol. Bonjour is Apple’s zero-configuration networking service, which basically means it’s how Apple devices find each other on a network. Our process, mDNSResponder, regularly scans your local network looking for other Bonjour-enabled devices.|
|158|mdt|??? försök ha på|
|159|mediaanalysisd|used for videos and photos maybe|
|160|mediaartworkd|iTunes Cover Art|
|161|medialibraryd|?? try to leave on|
|162|mediaremoted|MediaRemote is a framework that is used to communicate with the media server, mediaserverd. It can be utilized to query the server for now playing information, play or pause the current song, skip 15 seconds, etc.|
|163|mediaserverd|MediaRemote is a framework that is used to communicate with the media server, mediaserverd. It can be utilized to query the server for now playing information, play or pause the current song, skip 15 seconds, etc.|
|164|memory-maintenance|probably cleans RAM or something|
|165|midiserver-ios|midi such as MIDI|
|166|misagent|Provisioning profiles|
|167|MobileAccessoryUpdater|It seems that fud is the firmware update daemon of com.apple.MobileAccessoryUpdater that presumably is responsible for firmware downloads for bluetooth peripherals and running the firmware update daemon|
|168|mobileactivationd|Activation services. If you disable this, you may get temporary de-activation of your device, among other things.|
|169|mobileassetd|Mobileassetd is the daemon in charge of managing and downloading assets when other apps ask for them. Examples: dictionaries (for the user to see definitions of words), fonts, time zone database, firmware for accessories, voice recognition data for the Hey Siri feature, and OTA updates of the iOS firmware. I would recommend killing this daemon using CocoaTop and hopefully the issue should be fixed.|
|170|mobilecheckpoint|dont know, low info, leave alone|
|171|MobileFileIntegrity|checks file integrity|
|172|mobilegestalt|The libMobileGestalt.dylib, even though technically not a framework per se, it is of utmost importance, as it serves iOS as a central repository for all of the system's properties - both static and runtime. In that, it can be conceived as the parallel of OS X's Gestalt, which is part of CoreServices (in CarbonCore). But similarities end in name only. OS X's Gestalt is somewhat documented (in its header file), and has been effectively deprecared as of 10.8. MobileGestalt is entirely undocumented, and isn't going away any time soon.|
|173|MobileInternetSharing|3G-4G-5G InternetSharing|
|174|mobilestoredemod|storedemo function|
|175|mobilestoredemodhelper|storedemo function|
|176|mobiletimerd|alarm clock perhaps|
|177|mobilewatchdog|watchdogd is part of the watchdog infrastructure, it ensures that both the kernel and user spaces are making progress.If the kernel or user space is stuck, a reboot will be triggered by the watchdog infrastructure.|
|178|mstreamd|is the process that transports pictures and video located on apple servers|
|179|mtmergeprops|mergeProps is a function that handles combining the props passed directly to a compoment|
|180|nand_task_scheduler||
|181|navd|connected to gps and maps|
|182|ndoagent|new device outreach service|
|183|neagent-ios|Network Extension - Agent|
|184|nehelper-embedded|nehelper is part of the Network Extension framework. It is responsible for vending the Network Extension configuration to Network Extension clients and applying changes to the Network Extension configuration.|
|185|nesessionmanager|The nesessionmanager daemon If you look at the implementation of the ne_session_* functions, you will note that these functions are sending their request through XPC to the root dameon nesessionmanager located at the path /usr/libexec/nesessionmanager. This daemon is listening for commands and handles them in the method -(void)[NESMSession handleCommand:fromClient:]. By looking at the logging strings, you can find the code for each command: cstr_00072C74 "%.30s:%-4d %@: Ignore restart command from %@, a pending start command already exists" cstr_00072CCA "%.30s:%-4d %@: Stop current session as requested by an overriding restart command from %@" cstr_00072D7D "%.30s:%-4d %@: Received a start command from %@, but start was rejected" cstr_00072DFD "%.30s:%-4d %@: Received a start command from %@" cstr_00072E2D "%.30s:%-4d %@: Skip a %sstart command from %@: session in state %s" cstr_00072E73 "%.30s:%-4d %@: Received a stop command from %@ with reason %d" cstr_00072F7E "%.30s:%-4d %@: Received an enable on demand command from %@" For example when an IKEv2 service is started, the method -(void)[NESMIKEv2VPNSession createConnectParametersWithStartMessage:] will be called. The architecture of the daemon is out of the scope of this article.|
|186|NetworkLinkConditioner||
|187|networkserviceproxy|Apple Network Service Proxy executable|
|188|newsd|Apple News|
|189|newspolicyd||
|190|NoATWAKEUP|dont know, but disabling is widely done|
|191|notification_proxy|leave on|
|192|notifyd|It's a system daemon that runs in the background to communicate with the update server. Killing it wouldn't disable it, but you shouldn't disable it anyways... Photo app depends on it "Ipad Air"|
|193|nsurlsessiond|assume Safarirelated and such|
|194|nsurlstoraged|nsurlstoraged is the daemon that makes this local storage possible. Safari is the main application that actually uses this capability, but a number of other Apple programs also use it: Mail, Calendar, and iCloud,|
|195|obliteration|Erase device via AppleEffacableStorage|
|196|openssh||
|197|oscard|responsible for sensors such as autorotation|
|198|OTACrashCopier|Moves crashes from Over the Air software updates to /var/mobile/Library/Logs. Feel free to remove the daemon|
|199|OTATaskingAgent|Tells the device to periodically check for OTA updates. Feel free to remove.|
|200|parsecd|Siri related process I think|
|201|passd|Apple Passbook is a mobile application on an iPhone or iPod Touch that allows users to store .pkpass files called passes. Apple allows vendors to easily build passes that can be used as coupons, boarding passes, tickets, loyalty cards or gift certificates.|
|202|pasteboard|copy paste|
|203|perboardservice v2|dont touch PREBOARDSERVICE / First login screen|
|204|PerfPowerServicesExtended|PerfPowerServices -- manages structured log archives that enable retrieval of system power and performance data. DESCRIPTION The PerfPowerServices daemon works only within the context of a launchd job and should not be run from the command line.|
|205|personad||
|206|pfd|Packet Filter|
|207|photoanalysisd|Photo Library|
|208|pipelined|In computing, a pipeline, also known as a data pipeline,[1] is a set of data processing elements connected in series, where the output of one element is the input of the next one. The elements of a pipeline are often executed in parallel or in time-sliced fashion. Some amount of buffer storage is often inserted between elements.|
|209|pluginkit|pkd -- management and supervision daemon for plug-in services|
|210|powerd|When your Mac goes to sleep after being idle, powerd is what makes that happen|
|211|powerloghelperd|This is used to monitor any incompatibilities with 3rd party chargers.|
|212|PowerUIAgent|com.apple.PowerUIAgent process is associated with the optimized battery charging optio|
|213|preboardservice|first login screen LEAVE ALONE|
|214|printd|Delete this if you don't use AirPrint|
|215|dprivacyd|Differential Privacy|
|216|progressd|com.apple.progressd progressd is the ClassKit sync agent. It handles syncing classes, class members, student handouts and progress data between student and teacher man-aged Apple ID accounts.|
|217|protectedcloudstorage||
|218|ProxiedCrashCopier|Move crash reports from devices like the Apple WatchMove crash reports from devices like the Apple Watch|
|219|ptpd|Picture Transfer Protocol used to transfer images via usb|
|220|purplebuddy|related to restoration and backup from icloud "try to leave alone"|
|221|PurpleReverseProxy|has to do with image and restore and such try to leave alone|
|222|quicklook|Quicklook is the function that offers Finder built-in preview of any selected document in multicolumn Finder, or control-mouse click the file choosing the Quick Look menu, or File menu's Quicklook function. Previews in the Finder's Get Info window do the same thing|
|223|racoon|Virtual Private Network service on off|
|224|rapportd|Daemon that enables Phone Call Handoff and other communication features between Apple devices. Use '/usr/libexec/rapportd -V' to get the version.|
|225|recentsd|Most recently used|
|226|remotemanagementd|remotemanagementd handles HTTP communication with an Mobile Device Management (MDM) Version 2 server, delivering configuration information to the local Device Management daemon (dmd), and sending status messages back to the server.|
|227|replayd|Gamecenter instant replay function|
|228|ReportCrash|log files|
|229|ReportCrash.Jetsam|log files|
|230|Reportcrash.SimulateCrash|log files|
|231|ReportMemoryException|The ReportMemoryException command is a system service which should only be launched by launchd. It generates memory usage diagnostic logs as indicated by system events such as memory limit violations.|
|232|reversetemplated|Create user folder layout from template|
|233|revisiond|Historical file revision management|
|234|roleaccountd|?? leave alone This folder appears to be used for iOS updates,|
|235|rolld||
|236|routined|routined is a per-user daemon that learns historical location patterns of a user and predicts future visits to locations|
|237|rtcreportingd|looks to be a phone home to verify that your device is authorised for home sharing|
|238|Safari.SafeBrowsing||
|239|SafariBookmarksSyncAgent||
|240|SafariCloudHistoryPushAgent||
|241|safarifetcherd|Safari extension retrieves web pages and does a lot of the heavy lifting|
|242|SChelper|Smart Card helper|
|243|screensharigserver|AirPlay|
|244|scrod|voice control related|
|245|searchd|Spotlight|
|246|securityd|Handle keychains etc|
|247|securityuploadd|The securityuploadd daemon collects information about security events from the local system, and uploads them to Apple's Splunk servers in the cloud|
|248|SepUpdateTimer|Summertime Wintertime timer|
|249|sharingd|Generic "Share" action handler|
|250|sidecar-relay|Use iDevice as a screen|
|251|signpost_reporter|Signposts is a developer feature created by Apple to help developers diagnose performance problems in applications.|
|252|siriactionsd|Siri voice shortcuts|
|253|siriknowledged|Siri related extension|
|254|softwareupdated|update service|
|255|softwareupdateservicesd|Tells iOS how to start and execute an OTA update, feel free to remove. Although DO NOT attempt an OTA update with this removed. I feel that it also stops the update from happening if the device is jailbroken.|
|256|spindump|dumps information log of spinning "stuck" app|
|257|splashboardd|makes springboard function|
|258|SpringBoard||
|259|storage_mounter|The iPad Camera Connection Kit depends on this daemon. Delete if you don't use it, or if you don't have an iPad.|
|260|storebookkeeperd|probably has to do with book app|
|261|streaming_zip_conduit|Handle untrusted Zip content out of process|
|262|studentd|studentd manages the Apple Classroom experience for students and teachers that use MDM and the Apple School Manager service.|
|263|suggestd|suggestd is daemon that processes user content in order to detect contacts, events, named entities, etc. It receives content from Mail, Spotlight, Messages and other apps.|
|264|swcagent|Shared Web Credentials, uses NSURLSession to fetch the apple-app-site-association file.|
|265|swcd|Shared Web Credentials|
|266|symptomsd helper|symptomsd runs as part of the CrashReportor framework.|
|267|symptomsd helper|syncdefaultsd|
|268|sysdiagnose|DEBUGGING|
|269|sysdiagnose_helper|DEBUGGING|
|270|syslogd|Logs system events|
|271|systemstats|DEBUGGING systemstatsd is a "daemon" type process that generates data that the systemstats program can print out. It normally only runs when the systemstats program is actually run, which is only done manually from the Terminal as far as I know. It prints out a bunch of data that might be worth knowing. From a Terminal window you can|
|272|tailspind|DEBUGGING An application asks tailspind and spindump to take a snapshot of the state of that application and write it out to disk, or; Some application or process would consume maximum CPU for some period of time (30 seconds seems to be the general consensus), and then tailspind and spindump would fire up to take a snapshot of what was going on for future debugging purposes.|
|273|tccd|Total and Complete Control -TCC’s background service is tccd, whose only documented control is in tccutil, which merely clears existing settings from lists in the Privacy tab of the Security & Privacy pane. Its front end is that Privacy tab. In the unified log, TCC’s entries come from the subsystem com.apple.TCC.|
|274|telephonyutilites|telephony utilities|
|275|TextInput.kbd|input of text|
|276|timed|Network Time Protocol|
|277|timezoneupdates|TimezoneUpdates|
|278|tipsd|Tip of the day|
|279|touchsetupd|Touch Accommodations to fit your specific fine-motor skills needs. It's recommended that you configure your preferences|
|280|trustd|PKI trust evaluation // Required for web surfing, required for safe certificates|
|281|tvremoted|Apple TV remote app|
|282|tzlinkd|Time Zones|
|283|UsageTrackingAgent|UsageTrackingAgent monitors and reports usage budgets set by Health, Parental Controls, or Device Management.|
|284|usb-networking-addnetwork|usb/lightning to ethernet i think|
|285|UserEventAgent|The UserEventAgent utility is a daemon that loads system-provided plugins to handle high-level system events which cannot be monitored directly by launchd.3555|
|286|userfs_helper|user fileystem helper|
|287|userfsd|user filesystem?|
|288|VideoSubscriberAccount|Video Subscriber Account provides APIs to help you create apps that require secure communication with a TV provider’s authentication service. The framework also informs the Apple TV app about whether your user has a subscription and the details of that subscription.|
|289|videosubscriptionsd|/usr/libexec/videosubscriptionsd (more correctly id’ed as com.apple.VideoSubscriberAccount.videosubscriptionsd) is part of the single sign-on video subscription services that Apple introduced into OSX/tvOS/iOS. Its part of the Video Subscriber Account framework (VideoSubscriberAccount.framework) and specifically relates to authenticating for video streaming/playback.|
|290|voiced|Deals with voice control|
|291|voicemod|Deals with voice control "I think"|
|292|VoiceOverTouch|Voice Over Touch Function|
|293|wapic|Deals with errors with Wifi networks with Chinese characters in the name /can prob be deleted|
|294|watchlistd|dunno leave on|
|295|WebBookmarks|bookmarks web|
|296|webinspectord|webinspectord is the service in charge of all operations related to the use of the ‘Web Inspector’ on iOS. It runs an XPC service known as // webinspectord relays commands between Web Inspector and targets that it can remotely inspect, such as WKWebView and JSContext instances.|
|297|wifid|wifi ID (maybe pass and such|
|298|wififirmwareloaderlegacy|firmwarefile for wifi|
|299|wifivelocityd|used with wifi may be deactivated?|
|300|wirelessproxd|, maybe used with airdrop wifi related|
|301|WirelessRadioManager|WIFI|
|302|asd|daemon|
|303|synctodefaultsd|most likely related to icloud sync|
|304|cloudkeychainproxy3|icloud keychain|
|305|analytics||
|306|captiveagent|checks to see if you are on a captive wifi network, such as subscription hotspot or "Mcdonalds wifi"|
|307|timerd|timer function in clock app|
|308|pkd|manages plugins in safari launchd|
|309|Oscard|Core Motion Process accelerometer, gyroscope, pedometer, and environment-related events.|
|310|||
|311|||
|312||some daemons need a restart of the device to begin to function. Therefore troubleshooting is extremely troublesome, as you have to re-jailbreak multiple times if you are in a tethered JB situation. However, I've spent days compiling this list.|
|313||So wherever you start in your project. You allready got a head start thanks to this info.|
|314|imdpersistance|IMDPersistenceAgent. It tells you that it is part of Messages application. It provides a background process for persistent messaging to notification center and other items, especially Facetime. If you don't use any of that, go ahead and kill it but it will come back if you've enabled any messaging protocols.|
|315|cryptotokenkit|CryptoTokenKitAccess security tokens and the cryptographic assets they store.You use the CryptoTokenKit framework to easily access cryptographic tokens. Tokens are physical devices built in to the system, located on attached hardware (like a smart card), or accessible through a network connection. Tokens store cryptographic objects like keys and certificates. They also may perform operations—for example, encryption or digital signature verification—using these objects. You use the framework to work with a token’s assets as if they were part of your system, even though they remain secured by the token.|
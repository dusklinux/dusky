Conservative legend:

- `yes` = generally safe to disable for most personal devices
- `if-unused` = usually safe only if you do not use that feature
- `no` = do not disable, or too unclear/private to safely recommend disabling

|#|daemon|description|safe_to_disable_for_most_users|
|---|---|---|---|
|1|ABDatabaseDoctor|Contacts database repair helper|yes|
|2|abm-helper|Baseband or modem-related helper|no|
|3|absd|Address Book Source Daemon for Contacts accounts and sync|no|
|4|accessibility.axassetsd|Downloads or manages accessibility assets such as voices|if-unused|
|5|accessibility.axremoted|Remote accessibility services|if-unused|
|6|accessibility.heard|Hearing accessibility features and hearing aid support|if-unused|
|7|accessibility.motiontrackingd|Accessibility motion or head-tracking support|if-unused|
|8|accessoryupdaterd|Firmware updates for Apple or supported accessories|if-unused|
|9|accountsd|System accounts database and access management|no|
|10|activityawardsd|Fitness or Activity awards processing|if-unused|
|11|adid|Advertising identifier service|yes|
|12|AdminLite|Private Apple administrative helper, unclear public role|no|
|13|afcd|Apple File Conduit for USB file access and Finder or iTunes file sharing|yes|
|14|aggregated|System analytics aggregation|yes|
|15|aggregated.addaily|Advertising-related daily aggregation task|yes|
|16|akd|AuthKit daemon for Apple ID auth and tokens|no|
|17|amsaccountsd|Apple Media Services account handling|no|
|18|amsengagementd|Apple Media Services engagement telemetry|yes|
|19|analyticsd|System analytics and diagnostics collection|yes|
|20|and|Private Apple daemon, unclear public role|no|
|21|announced|Announce Notifications or Announce Messages support|if-unused|
|22|anomalydetectiond|System anomaly detection and reporting|yes|
|23|ap.adprivacyd|Advertising privacy policy enforcement|yes|
|24|ap.promotedcontentd|Promoted content and App Store recommendations or ads|yes|
|25|appleaccountd|Apple Account management and sync|no|
|26|applecamerad|Core camera hardware interface|no|
|27|AppleCredentialManagerDaemon|Apple credential management service|no|
|28|AppSSODaemon|Single Sign-On service, mainly enterprise|yes|
|29|appstorecomponentsd|App Store support components|no|
|30|appstored|Core App Store download and update service|no|
|31|apsd|Apple Push Notification service daemon|no|
|32|arkitd|ARKit service for augmented reality apps|if-unused|
|33|asd|Private Apple service daemon, unclear public role|no|
|34|askpermissiond|Family Sharing Ask to Buy approvals|yes|
|35|AssetCacheLocatorService|Finds local Apple caching servers on the network|yes|
|36|assetsd|Photos asset library management|no|
|37|assetsd.nebulad|Photos or iCloud Photos asset sync helper|if-unused|
|38|assistant_cdmd|Siri assistant data management|if-unused|
|39|atc|Legacy Finder or iTunes sync helper|yes|
|40|AuthenticationServicesCore.AuthenticationServi...|AuthenticationServices XPC service for sign-in and passkey flows|no|
|41|avatarsd|Memoji or avatar rendering and sync|if-unused|
|42|avconferenced|Audio and video conferencing service|if-unused|
|43|backboardd|Core iOS input, display, and event routing|no|
|44|backgroundassets.user|Background asset downloads for apps and system content|no|
|45|BarcodeSupport.BarcodeNotificationService|Barcode support service|if-unused|
|46|batteryintelligenced|Battery intelligence and optimized charging support|yes|
|47|betaenrollmentd|Beta enrollment management|yes|
|48|biomed|Biome on-device knowledge store service|yes|
|49|biomesyncd|Biome sync service|yes|
|50|biometrickitd.mesa|Biometric hardware interface for Touch ID or Face ID|no|
|51|bird|iCloud Drive or CloudDocs sync daemon|if-unused|
|52|BlueTool|Bluetooth controller bring-up and management|no|
|53|bluetoothd|Core Bluetooth stack daemon|no|
|54|bluetoothuiservice|Bluetooth pairing and UI support|if-unused|
|55|bluetoothuserd|User-level Bluetooth support|if-unused|
|56|bookassetd|Apple Books asset management|yes|
|57|bookdatastored|Apple Books data storage and sync|yes|
|58|bootps|BOOTP or DHCP service used for tethering-related features|yes|
|59|BTServer.avrcp|Bluetooth media control profile support|if-unused|
|60|BTServer.le|Bluetooth Low Energy support|if-unused|
|61|BTServer.map|Bluetooth Message Access Profile support|if-unused|
|62|BTServer.pbap|Bluetooth Phone Book Access Profile support|if-unused|
|63|businessservicesd|Apple business messaging or related services|yes|
|64|cache_delete|Storage cleanup and cache purge service|no|
|65|calaccessd|Calendar database access control|no|
|66|CallHistorySyncHelper|iCloud sync helper for call history|yes|
|67|captiveagent|Captive portal detection for public Wi-Fi|if-unused|
|68|carkitd|CarPlay core daemon|if-unused|
|69|CarPlayApp|CarPlay user interface process|if-unused|
|70|cdpd|Private data-protection or escrow-related security service|no|
|71|certui.relay|Certificate trust prompt relay|no|
|72|cfnetwork.AuthBrokerAgent|Network authentication broker|no|
|73|cfnetwork.cfnetworkagent|Core networking agent|no|
|74|cfprefsd.xpc.daemon|CoreFoundation preferences daemon|no|
|75|cfprefsd.xpc.daemon.system|System-level preferences daemon|no|
|76|checkerboardd|Recovery or diagnostics UI service|yes|
|77|chronod|Widget timelines and scheduled widget updates|if-unused|
|78|ckdiscretionaryd|CloudKit background transfer service|if-unused|
|79|ClarityBoard|Assistive Access or simplified accessibility UI|yes|
|80|ClipServices.clipserviced|App Clips management service|yes|
|81|cloudd|CloudKit syncing daemon|if-unused|
|82|cloudpaird|iCloud-based pairing info sync|if-unused|
|83|cloudphotod|iCloud Photos background sync|if-unused|
|84|CloudSettingsSyncAgent|iCloud settings sync agent|if-unused|
|85|cmfsyncagent|Private Apple sync agent, unclear public role|no|
|86|commandandcontrol|Voice Control accessibility daemon|if-unused|
|87|CommCenter|Core cellular and telephony daemon|no|
|88|CommCenter-noncellular|Network service variant for non-cellular devices|no|
|89|CommCenterMobileHelper|Cellular helper for carrier or config tasks|no|
|90|CommCenterRootHelper|Privileged cellular configuration helper|no|
|91|companion_proxy|Apple Watch companion proxy service|if-unused|
|92|companionauthd|Apple Watch authentication and pairing support|if-unused|
|93|configd|System Configuration and network configuration|no|
|94|contacts.donation-agent|Contacts suggestions donation agent|yes|
|95|contactsd|Contacts database daemon|no|
|96|containermanagerd|App sandbox and container manager|no|
|97|containermanagerd.system|System-level container manager|no|
|98|contextstored|Stores context for Siri or proactive features|yes|
|99|coordinated|File or document coordination service|no|
|100|CoreAuthentication.daemon|Local authentication service used by biometric or passcode prompts|no|
|101|corecaptured|Low-level diagnostic capture service|yes|
|102|coredatad|Core Data support daemon used by some apps and frameworks|no|
|103|coreduetd|On-device usage learning and prediction service|no|
|104|coreidvd|Digital ID or Wallet identity verification support|yes|
|105|corerepaird|Repair status or parts validation service|yes|
|106|coreservices.useractivityd|NSUserActivity and Handoff support|if-unused|
|107|corespeechd|Core speech service for Siri or voice trigger support|if-unused|
|108|corespotlightservice|Core Spotlight indexing and search support|no|
|109|countryd|Country or region determination service|no|
|110|ctkd|CryptoTokenKit daemon for smart cards and token credentials|yes|
|111|dasd|Duet Activity Scheduler for background tasks|no|
|112|dataaccess.dataaccessd|Exchange, CalDAV, and CardDAV sync service|if-unused|
|113|DataDetectorsSourceAccess|Data Detectors source access service|no|
|114|datastored|Private Apple data storage service, unclear public role|no|
|115|deferredmediad|Deferred camera and photo processing service|no|
|116|deleted_helper|Helper for cache deletion and storage cleanup|no|
|117|DesktopServicesHelper|CoreServices file or metadata helper|no|
|118|device-o-matic|Private Apple device or hardware helper service|no|
|119|deviceaccessd|Device access or permission mediation service|no|
|120|devicecheckd|DeviceCheck or App Attest support service|no|
|121|devicedatasetresd|Private Apple service, unclear public role|no|
|122|devicemanagementclient.mdmd|Managed device management client component|yes|
|123|devicemanagementclient.teslad|Managed enrollment or device management helper|yes|
|124|dhcp6d|DHCPv6 client daemon|no|
|125|diagnosticd|System diagnostics processing|yes|
|126|diagnosticextensionsd|Diagnostic extension collection|yes|
|127|dietapplecamerad|Lightweight camera service used by specific pipelines|no|
|128|dietappleh13camerad|Hardware-specific lightweight camera daemon|no|
|129|diskarbitrationd|Disk arbitration and mount management|no|
|130|diskimagesiod|Disk image mount support service|yes|
|131|diskimagesiod.ram|RAM disk image support service|yes|
|132|distnoted.xpc.daemon|Distributed notifications service|no|
|133|dmd|Declarative Device Management daemon|yes|
|134|DMHelper|Device management helper|yes|
|135|donotdisturbd|Do Not Disturb and Focus management|if-unused|
|136|dprivacyd|Differential Privacy telemetry service|yes|
|137|DragUI.druid|Drag and Drop UI support|if-unused|
|138|driverkitd|DriverKit user-space driver host service|no|
|139|dt.automationmode-writer|Developer automation mode support task|yes|
|140|dt.AutomationModeUI|Developer automation mode UI service|yes|
|141|dt.fetchsymbolsd|Developer symbol fetch service|yes|
|142|dt.previewsd|Developer preview support service|yes|
|143|dt.remotepairingdeviced|Developer wireless pairing daemon|yes|
|144|duetexpertd|Duet or Siri suggestions expert system|yes|
|145|email.maild|Apple Mail background sync and fetch|if-unused|
|146|EscrowSecurityAlert|Security escrow alert service|yes|
|147|exchangesyncd|Microsoft Exchange sync daemon|yes|
|148|facemetricsd|Face ID metrics and attention support|no|
|149|factory.NFQRCoded|Factory or diagnostic QR or NFC code support|yes|
|150|fairplayd.H2|FairPlay DRM daemon|no|
|151|fairplaydeviceidentityd|FairPlay device identity support|no|
|152|familycircled|Family Sharing management daemon|yes|
|153|FamilyControlsAgent|Family Controls or Screen Time family enforcement|yes|
|154|familynotificationd|Family Sharing notifications daemon|yes|
|155|fdrhelper|Factory restore or calibration key helper|no|
|156|FileCoordination|File coordination service|no|
|157|FileProvider|Files app and third-party file provider support|if-unused|
|158|filesystems.apfs_iosd|APFS filesystem management daemon|no|
|159|filesystems.livefileproviderd|Live File Provider filesystem support|if-unused|
|160|filesystems.smbclientd|SMB client daemon for network shares|yes|
|161|filesystems.userfs_helper|User-space filesystem helper|no|
|162|filesystems.userfsd|User-space filesystem daemon|no|
|163|financed|Apple financial services support|yes|
|164|fitcore|Fitness framework core service|yes|
|165|fitcore.session|Fitness active session support service|yes|
|166|fitnesscoachingd|Fitness coaching notifications|yes|
|167|followupd|Settings follow-up suggestions and account reminders|yes|
|168|fontservicesd|System font management and font downloads|no|
|169|fs_task_scheduler|Filesystem maintenance scheduler|no|
|170|fseventsd|Filesystem events daemon|no|
|171|ftp-proxy-embedded|Legacy FTP proxy service|yes|
|172|fullkeyboardaccess|Accessibility Full Keyboard Access support|yes|
|173|fusiond|Private Apple fusion service, unclear public role|no|
|174|GameController.gamecontrollerd|Game controller framework daemon|yes|
|175|geod|GeoServices daemon for Maps and geocoding|no|
|176|gpsd|GPS hardware daemon|no|
|177|griddatad|Private Apple grid or layout data service|no|
|178|GSSCred|Kerberos credential daemon|yes|
|179|handwritingd|Handwriting recognition service|if-unused|
|180|hangreporter|Reports app or system hangs|yes|
|181|hangtracerd|Collects traces for app or system hangs|yes|
|182|healthappd|Health app support daemon|if-unused|
|183|healthrecordsd|Health Records sync and storage service|yes|
|184|icloud.findmydeviced|Find My device support daemon|if-unused|
|185|icloud.fmfd|Find My people support daemon|yes|
|186|icloud.fmflocatord|Find My location reporting daemon|if-unused|
|187|icloud.searchpartyd|Find My network or AirTag crowd-location daemon|if-unused|
|188|icloudsubscriptionoptimizerd|iCloud subscription or storage upsell service|yes|
|189|iconservices.iconservicesagent|App icon rendering and cache service|no|
|190|idamd|Private Apple identity or auth daemon|no|
|191|idcredd|Identity credential service for digital IDs|yes|
|192|identityservicesd|Identity Services daemon for iMessage and FaceTime|no|
|193|idscredentialsagent|Identity Services credential agent|no|
|194|idsremoteurlconnectionagent|Identity Services network agent|no|
|195|imagent|iMessage account and messaging agent|no|
|196|imautomatichistorydeletionagent|Automatic message history deletion support|yes|
|197|imtransferagent|Transfers iMessage attachments and media|no|
|198|inboxupdaterd|Inbox update helper, public role not well documented|no|
|199|ind|Private Apple daemon, unclear public role|no|
|200|installcoordination_proxy|Proxy helper for install coordination|no|
|201|installcoordinationd|Coordinates app installs and updates|no|
|202|intelligenceplatformd|On-device intelligence platform for Siri and predictions|if-unused|
|203|IOAccelMemoryInfoCollector|GPU or graphics memory diagnostics collector|yes|
|204|iomfb_bics_daemon|Display subsystem calibration or framebuffer helper|no|
|205|iomfb_fdr_loader|Factory display calibration loader|no|
|206|iosdiagnosticsd|Apple diagnostics and support tool daemon|yes|
|207|ioupsd|Private power-related service, unclear public role|no|
|208|itunesstored|Store services daemon for media purchases and downloads|if-unused|
|209|jetsamproperties.D21|Memory pressure and Jetsam configuration|no|
|210|jetsamproperties.D211|Hardware-specific Jetsam configuration|no|
|211|keychainsharingmessagingd|Keychain sharing or iCloud Keychain messaging support|if-unused|
|212|knowledgeconstructiond|Builds knowledge data for Siri or search|yes|
|213|languageassetd|Downloads language assets such as dictionaries or voices|if-unused|
|214|linkd|Linking service for intents, deep links, or app routing|no|
|215|liveactivitiesd|Live Activities support|if-unused|
|216|localizationswitcherd|Localization or language-switch helper|if-unused|
|217|locationd|Core Location daemon|no|
|218|locationpushd|Location-triggered push notification support|if-unused|
|219|logd|Unified logging daemon|no|
|220|logd_helper|Helper for unified logging|no|
|221|lsd|LaunchServices daemon for app registration and launching|no|
|222|lsd.system|System-level LaunchServices daemon|no|
|223|lskdd|Private Apple daemon, unclear public role|no|
|224|managedconfiguration.passcodenagd|Managed passcode compliance prompt service|yes|
|225|managedconfiguration.profiled|Configuration profile and managed policy daemon|if-unused|
|226|ManagedSettingsAgent|Managed Settings or Screen Time enforcement|if-unused|
|227|maps.destinationd|Maps destination suggestions service|yes|
|228|maps.geocorrectiond|Maps location correction or feedback daemon|yes|
|229|maps.pushdaemon|Maps notification daemon|yes|
|230|matd|Private Apple measurement or telemetry daemon|no|
|231|mDNSResponder|Bonjour and Multicast DNS daemon|no|
|232|mDNSResponderHelper|Helper for Bonjour or mDNS networking|no|
|233|mdt|Private Apple daemon, unclear public role|no|
|234|mediaanalysisd|On-device media analysis for Photos|yes|
|235|mediaanalysisd.service|XPC service for media analysis tasks|yes|
|236|mediaartworkd|Downloads or manages album art and artwork|yes|
|237|medialibraryd|Media library database daemon|if-unused|
|238|mediaparserd|Parses media files and containers|no|
|239|mediaremoted|Remote media control daemon|no|
|240|mediaserverd|Core audio and video playback service|no|
|241|mediasetupd|Media device setup support such as HomePod or Apple TV|yes|
|242|mediastream.mstreamd|Shared Photo Streams or related media stream service|yes|
|243|memory-maintenance|Memory pressure housekeeping task|no|
|244|merchantd|Apple Pay merchant session or transaction support|if-unused|
|245|metrickitd|MetricKit performance metrics collection|yes|
|246|misagent|Provisioning profile and signing support agent|if-unused|
|247|mlmodelingd|Downloads or manages machine learning models|yes|
|248|mlruntimed|Core ML runtime daemon|no|
|249|mobile.assertion_agent|Manages process assertions and background execution claims|no|
|250|mobile.cache_delete_app_container_caches|Clears app container caches|no|
|251|mobile.cache_delete_daily|Daily cache cleanup task|no|
|252|mobile.cache_delete_mobile_backup|Cache cleanup related to device backups|yes|
|253|mobile.heartbeat|System heartbeat or watchdog-related service|no|
|254|mobile.house_arrest|USB file sharing service for app containers|yes|
|255|mobile.insecure_notification_proxy|Legacy lockdown notification proxy for host communication|if-unused|
|256|mobile.installd|Core app installation daemon|no|
|257|mobile.keybagd|Keybag and data-protection key management|no|
|258|mobile.lockdown|Lockdown pairing, trust, and host communication|if-unused|
|259|mobile.MCInstall|Mobile configuration install helper|yes|
|260|mobile.notification_proxy|Lockdown notification proxy for host communication|if-unused|
|261|mobile.storage_mounter|Mount service for developer or external storage images|if-unused|
|262|mobile.storage_mounter_proxy|Proxy for storage mount operations|if-unused|
|263|mobile.usermanagerd|User management service, mostly Shared iPad scenarios|yes|
|264|mobile_installation_proxy|Proxy service for app installation operations|no|
|265|mobileactivationd|Activation status and server communication daemon|no|
|266|mobileassetd|Downloads Apple mobile assets such as trust or language files|no|
|267|mobilebackup2|Finder or iTunes local backup service|yes|
|268|mobilecheckpoint.checkpointd|Update or checkpoint validation daemon|if-unused|
|269|MobileFileIntegrity|AMFI code-signing enforcement|no|
|270|mobilegestalt.xpc|System capability and hardware property query service|no|
|271|mobilerepaird|Parts pairing and repair status service|if-unused|
|272|mobilestoredemod|Retail demo mode daemon|yes|
|273|mobilestoredemodhelper|Retail demo mode helper|yes|
|274|mobiletimerd|Clock alarms and timers support|no|
|275|momentsd|Photos Memories and moments curation|yes|
|276|MTLAssetUpgraderD|Metal asset or shader upgrade service|no|
|277|mtmergeprops|Multi-touch calibration or property merge support|no|
|278|nand.aspcarry|Private NAND or flash-storage support task|no|
|279|nand_task_scheduler|Flash storage maintenance scheduler|no|
|280|nanobackupd|Apple Watch backup daemon|yes|
|281|nanoprefsyncd|Apple Watch preference sync daemon|yes|
|282|nanoregistryd|Apple Watch pairing registry daemon|yes|
|283|nanoregistrylaunchd|Apple Watch registry launch helper|yes|
|284|nanotimekitcompaniond|Apple Watch face or TimeKit companion sync|yes|
|285|naturallanguaged|Natural Language framework daemon|if-unused|
|286|navd|Turn-by-turn navigation service for Maps|if-unused|
|287|ndoagent|Private Apple daemon, unclear public role|no|
|288|neagent-ios|Network Extension agent for VPNs and filters|if-unused|
|289|nearbyd|Nearby interaction and proximity service|if-unused|
|290|nehelper-embedded|Network Extension helper service|if-unused|
|291|nesessionmanager|Manages Network Extension sessions such as VPNs|if-unused|
|292|NetworkLinkConditioner|Developer network throttling tool|yes|
|293|networkserviceproxy|Network proxy service used by Private Relay and related features|if-unused|
|294|newsd|Apple News background service|yes|
|295|nexusd|Private Apple network-related daemon|no|
|296|nfcd|NFC hardware daemon|if-unused|
|297|nfrestore|NFC firmware or restore support service|if-unused|
|298|notifyd|Darwin notification center daemon|no|
|299|nsurlsessiond|Background transfer daemon for URLSession networking|no|
|300|online-auth-agent.xpc|Online authentication helper for sign-in or activation flows|if-unused|
|301|osanalytics.osanalyticshelper|OS analytics formatting helper|yes|
|302|ospredictiond|System prediction daemon|yes|
|303|parsec-fbf|Feedback or analytics service for Siri Search or Spotlight|yes|
|304|parsecd|Siri Search or Spotlight web query service|if-unused|
|305|pasteboard.pasted|System pasteboard or clipboard daemon|no|
|306|pcapd|Packet capture daemon for diagnostics|yes|
|307|peakpowermanagerd|Performance and battery aging management daemon|no|
|308|peopled|People intelligence daemon for contact suggestions|yes|
|309|perfdiagsselfenabled|Performance diagnostics task|yes|
|310|PerfPowerServicesExtended|Extended power and performance telemetry|yes|
|311|pfd|Packet Filter or firewall support daemon|no|
|312|photoanalysisd|Photos analysis daemon for faces, objects, and scenes|yes|
|313|photos.ImageConversionService|Image conversion service used by Photos and system frameworks|no|
|314|photos.VideoConversionService|Video conversion service used by Photos and system frameworks|no|
|315|pipelined|Image or camera processing pipeline daemon|no|
|316|pluginkit.pkd|PlugInKit daemon for app extensions and widgets|no|
|317|pluginkit.pkreporter|PlugInKit diagnostics reporter|yes|
|318|pointerUI.pointeruid|Pointer UI daemon for mouse or trackpad support|if-unused|
|319|powerd|Core power management daemon|no|
|320|powerdatad|Battery usage data collection and reporting|yes|
|321|powerlogHelperd|Power logging helper|yes|
|322|PowerUIAgent|Power and battery-related UI alerts agent|no|
|323|preboardservice|Pre-boot security or passcode UI service|no|
|324|preboardservice_v2|Updated variant of pre-boot security UI service|no|
|325|privacyaccountingd|App Privacy Report accounting service|yes|
|326|proactiveeventtrackerd|Tracks events for proactive Siri suggestions|yes|
|327|progressd|System progress reporting daemon|no|
|328|protectedcloudstorage.protectedcloudkeysyncing|Protected cloud key syncing service|no|
|329|ProxiedCrashCopier.ProxyingDevice|Copies crash logs from proxied or paired devices|yes|
|330|proximitycontrold|Proximity-based handoff or device interaction service|if-unused|
|331|ptpcamerad|PTP camera access daemon for importing photos|yes|
|332|ptpd|PTP daemon used for photo transfer or related sync|yes|
|333|purplebuddy.budd|Setup Assistant daemon|if-unused|
|334|PurpleReverseProxy|Reverse proxy used by some USB or device communication flows|if-unused|
|335|quicklook.ThumbnailsAgent|Quick Look thumbnail generation service|if-unused|
|336|rapportd|Continuity and device proximity or trust service|if-unused|
|337|recentsd|Recent contacts and suggestions daemon|yes|
|338|relatived|Motion or sensor-relative tracking service|if-unused|
|339|remindd|Reminders daemon for database and sync support|if-unused|
|340|remoted|Apple remote services daemon, public role not fully clear|no|
|341|RemoteManagementAgent|Remote management agent for supervised or managed devices|yes|
|342|remotemanagementd|Remote management daemon for supervised or managed devices|yes|
|343|replayd|ReplayKit screen recording and broadcast daemon|if-unused|
|344|ReportMemoryException|Reports out-of-memory events|yes|
|345|RestoreRemoteServices.restoreserviced|Restore support service used by host restore workflows|if-unused|
|346|retimerd|Private Apple hardware service, unclear public role|no|
|347|reversetemplated|Private Apple service, unclear public role|no|
|348|revisiond|Document revision or versioning support service|if-unused|
|349|routined|Significant locations and routine learning daemon|yes|
|350|rtcreportingd|Real-time communication analytics and reporting|yes|
|351|runningboardd|Process lifecycle and resource management daemon|no|
|352|Safari.History|Safari history support service|if-unused|
|353|Safari.passwordbreachd|Safari password breach checking service|yes|
|354|Safari.SafeBrowsing.Service|Safari fraudulent website check service|yes|
|355|SafariBookmarksSyncAgent|Safari bookmarks sync agent|yes|
|356|safarifetcherd|Safari Reading List offline fetcher|yes|
|357|safetyalertsd|Safety alerts service such as Emergency SOS and crash-related alerts|no|
|358|SCHelper|SystemConfiguration helper|no|
|359|screensharingserver|Screen sharing or remote screen service|yes|
|360|ScreenTimeAgent|Screen Time tracking agent|yes|
|361|scrod|Accessibility screen recognition or OCR daemon|if-unused|
|362|SecureBackupDaemon|Secure backup support daemon|no|
|363|security.CircleJoinRequested|iCloud Keychain trust-circle approval service|if-unused|
|364|security.cloudkeychainproxy3|iCloud Keychain proxy daemon|if-unused|
|365|security.cryptexd|Cryptex and Rapid Security Response management|no|
|366|security.swcagent|Shared Web Credentials agent|no|
|367|securityd|Core security and authorization daemon|no|
|368|securityuploadd|Security telemetry or report upload daemon|yes|
|369|seld|Secure Element daemon for Apple Pay and related operations|if-unused|
|370|SensorKitALSHelper|SensorKit ambient light helper|yes|
|371|sensorkitd|SensorKit daemon for approved sensor APIs|yes|
|372|SepUpdateTimer|Secure Enclave update scheduling task|no|
|373|seserviced|Secure Element services daemon|if-unused|
|374|sharingd|AirDrop, Handoff, and Continuity sharing daemon|if-unused|
|375|shazamd|Music recognition daemon for Shazam integration|yes|
|376|sidecar-relay|Sidecar relay service|yes|
|377|signpost.signpost_reporter|Performance signpost reporting task|yes|
|378|siriactionsd|Siri actions and shortcuts execution service|if-unused|
|379|siriinferenced|On-device Siri inference daemon|yes|
|380|siriknowledged|Siri knowledge cache daemon|yes|
|381|sirittsd|Siri text-to-speech daemon|if-unused|
|382|sleepd|Sleep schedule and sleep-related health features daemon|if-unused|
|383|sntpd|Simple Network Time Protocol daemon|no|
|384|sociallayerd|Shared with You and related social content integration|yes|
|385|softposreaderd|Tap to Pay on iPhone or SoftPOS reader service|yes|
|386|sosd|Emergency SOS service daemon|no|
|387|speechmodeltrainingd|On-device speech personalization and training|yes|
|388|spindump|Collects spindump diagnostics|yes|
|389|splashboardd|Launch screen snapshot and splash screen management|no|
|390|sportsd|Sports data background service for Apple apps|yes|
|391|Spotlight.IndexAgent|Spotlight indexing agent|no|
|392|spotlightknowledged|Spotlight knowledge integration daemon|yes|
|393|SpringBoard|Primary iOS home screen and app UI manager|no|
|394|StatusKitAgent|Focus status sharing agent|yes|
|395|storagedatad|Storage accounting and iPhone Storage calculations|yes|
|396|storagekitd|Storage framework daemon|no|
|397|storebookkeeperd|Store-related bookkeeping such as play state or metadata|yes|
|398|storekitd|StoreKit and in-app purchase daemon|no|
|399|streaming_zip_conduit|Streamed archive conduit used by installation or host tooling|if-unused|
|400|subridged|Apple Watch bridge service|yes|
|401|suggestd|Suggestions daemon for Mail, Messages, and proactive features|yes|
|402|swcd|Shared Web Credentials and associated domains daemon|no|
|403|symptomsd|Network diagnostics and symptom detection daemon|yes|
|404|symptomsd-diag|Detailed network diagnostics task|yes|
|405|symptomsd.helper|Helper for network diagnostics and symptom reporting|yes|
|406|synapse.contentlinkingd|Content linking service for Apple note-taking or cross-app linking|yes|
|407|SyncAgent|Legacy sync agent for media or content sync|yes|
|408|syncdefaultsd|Syncs some defaults or preferences via iCloud|if-unused|
|409|sysdiagnose|System diagnostics collection task|yes|
|410|sysdiagnose.darwinos|Low-level Darwin OS diagnostics task|yes|
|411|sysdiagnose_helper|Helper for system diagnostics collection|yes|
|412|systemstats.microstackshot_periodic|Periodic microstackshot task for performance and power stats|yes|
|413|tailspind|Collects performance degradation diagnostics|yes|
|414|tccd|Privacy permissions daemon|no|
|415|telephonyutilities.callservicesd|Call services daemon for CallKit, phone calls, and VoIP|no|
|416|terminusd|Private Apple network-path service, unclear public role|no|
|417|TextInput.kbd|Keyboard text input service|no|
|418|thermalmonitord|Thermal monitoring and throttling daemon|no|
|419|ThreadCommissionerService|Thread networking commissioner service for smart-home accessories|yes|
|420|timed|System clock and time maintenance daemon|no|
|421|timesync.audioclocksyncd|Audio clock sync service|if-unused|
|422|timezoneupdates.tzd|Time zone data update daemon|no|
|423|touchsetupd|Device-to-device setup and transfer helper|yes|
|424|translationd|System translation framework daemon|yes|
|425|transparencyd|Apple transparency verification daemon for security features|no|
|426|transparencyStaticKey|Static key or support component for transparency services|no|
|427|triald|Feature flag and experimentation daemon|yes|
|428|trustd|Certificate trust evaluation daemon|no|
|429|tvremoted|Apple TV Remote support daemon|yes|
|430|tzlinkd|Private Apple time zone helper daemon|no|
|431|uikit.eyedropperd|UIKit color picker or eyedropper support|yes|
|432|UsageTrackingAgent|Usage tracking agent for Screen Time or related analytics|yes|
|433|usb.networking.addNetworkInterface|USB networking interface setup task|if-unused|
|434|usbsmartcardreaderd|USB smart card reader daemon|yes|
|435|UserEventAgent-System|System user event scheduling and launch agent|no|
|436|videosubscriptionsd|TV provider or video subscription single sign-on daemon|yes|
|437|voicemail.vmd|Visual Voicemail daemon|if-unused|
|438|voicememod|Voice Memos background support daemon|yes|
|439|VoiceOverTouch|VoiceOver touch interaction support daemon|if-unused|
|440|watchdogd|System watchdog daemon|no|
|441|watchlistd|TV app watchlist or Up Next sync daemon|yes|
|442|watchpresenced|Apple Watch proximity or presence detection daemon|yes|
|443|wcd|WatchConnectivity daemon for Apple Watch apps|yes|
|444|weatherd|Weather framework and weather data service|if-unused|
|445|WebBookmarks.webbookmarksd|Web bookmarks daemon used by Safari and related apps|if-unused|
|446|webinspectord|WebKit remote inspection daemon|yes|
|447|webkit.adattributiond|WebKit ad attribution or Private Click Measurement daemon|yes|
|448|webkit.webpushd|WebKit web push notifications daemon|if-unused|
|449|wifi.hostapd|Wi-Fi hotspot access point daemon for Personal Hotspot|if-unused|
|450|wifi.wapic|WAPI Wi-Fi security support daemon|yes|
|451|wifianalyticsd|Wi-Fi analytics and telemetry daemon|yes|
|452|wifid|Core Wi-Fi network management daemon|no|
|453|WiFiFirmwareLoader|Wi-Fi firmware loader service|no|
|454|wifip2pd|Peer-to-peer Wi-Fi daemon used by AirDrop and AirPlay|if-unused|
|455|wifivelocityd|Wi-Fi diagnostics and measurement daemon|yes|
|456|WirelessRadioManager|Coordinates wireless radios such as cellular, Wi-Fi, and Bluetooth|no|
|457|xpc.roleaccountd|Private Apple XPC or account-role service, unclear public role|no|

If you want, next I can turn this into a cleaner “disable candidate” list containing only the rows marked `yes` and `if-unused`, so you do not have to manually filter the whole table.

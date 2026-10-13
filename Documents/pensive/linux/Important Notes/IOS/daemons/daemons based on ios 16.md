## Current state — v1.0.20, 2026-10-12

The matching app/config preset contains **512 targets: 318 Off / 194 On** (105 `no`, 89 Critical `NO`). The config is a builtin target list, not a snapshot of every manually customized phone setting. Installing an update does not apply defaults. Versioned test sections below retain their historical counts, classifications and pending checks; this summary and the latest feature results supersede older statements of current behavior.

The user identifies the current A11 jailbreak as **palera1n**, with no device passcode enabled. Earlier notes recorded Dopamine; that attribution is not the current setup. Preserve the existing passcode/jailbreak configuration. The tested runtime is iOS 16.7.16 with rootless `/var/jb` and Python 3.9.9. The bootstrap account home `/var/jb/var/mobile` differs from native iOS `/var/mobile`; SpringBoard preferences and its runtime report use the native path.

Keep repaired Critical dependencies enabled: pkd, nehelper (and its filename alias), searchd, biomed, syncdefaultsd, mobile.lockdown, appleaccountd/akd/adid, absd, imagent, identityservicesd, cdpd, followupd, both Photos conversion services, passd, ProtectedCloudKeySyncing, SecureBackupDaemon, contextstored and dasd, alongside the existing core Critical services. Uppercase `NO` records that classification. Pkd's Off → CPU loop → On recovery was reproduced; searchd's individual CPU causality was not isolated. Some account dependencies were restored as a set, not individually isolated.

Overrides has feature subpages and menu On/Off buttons. On enables listed jobs and known prerequisites; Off preserves Critical and dependency-only shared jobs. Individual Critical switches retain warnings. Shared labels in different groups refer to the same native override. Catalog switches load/unload their jobs; broader batches require userspace restart. Tools contains Import / Export Config. Descriptions remain visible without expanding rows; Search, available MiB and theme share one row without pinned category banners.

| Feature | Verified behavior / normal phone state |
| --- | --- |
| Screen recording | ReplayKitAngel + replayd; enable both and respring. Both group directions passed; normally Off. Camera videos use a separate path. |
| AirDrop / Wallet / accessibility | AirDrop policy and UI host Off; sharingd stays On. Wallet UI Off but Critical passd On for tested App Store confirmation. AccessibilityUIServer is optional with no Recommended On badge; builtin On, manually Off on this phone. |
| Accounts / sharing / store | Sign-in and SMS, Photos image/video WhatsApp export and two free-app downloads passed after the recorded repairs. FaceTime calls and paid purchases/Apple Pay remain untested; WhatsApp Meta AI shortcut remains unresolved, while Set Flashlight works. |
| iCloud Drive / Photos | Fresh-restart Drive export and Photos upload were confirmed on the web. Sync jobs bird/cloudd/cloudphotod/assetsd.nebulad returned Off by request; repaired Critical account/key/context/scheduler jobs stay On. |
| Lock Screen media controls | PosterBoard + liveactivitiesd On and userspace restart restored clear playback controls; PaperBoard was not required. Both group directions passed. All three hosts default Off and are normally Off; PosterBoard is shared with Wallpaper. |
| Widgets | chronod updates can be stopped; keep pkd On. Today View remains accessible because the attempted page-removal hooks failed and were removed. |
| SpringBoard interfaces | Control Center and App Library access switches passed On/Off after resprings in v1.0.20 and are saved Off. Settings are separate from daemon.cfg. Dock/status-bar hiding switches and hooks were removed because they only hid views; both interfaces are restored. RAM savings remain unverified. |
| Memory stress | Memory provides +100 MiB, −100 MiB and Clear, with fresh varied random data, allocation/footprint/headroom readings and cleanup on panel close. User checks passed allocation, minus, Clear and close/reopen cleanup. It consumes real memory for stress testing; it does not increase physical RAM, guarantee cache eviction or disable jetsam. |

The installed v1.0.20/build 21 source is `ca663064295eb14b72a669ececcfcf37eeda7277`; the later app documentation commit does not change that build. CI passed 23 Python tests, Swift checks and iOS packaging. Both saved launchd domains matched the pre-interface profile after testing. Short settled CPU samples and feature checks do not prove net RAM/battery gains, every service combination or full-system stress reliability.

## Reference classifications and historical test record

Most conservative revision I can stand behind. Undocumented/private daemons are intentionally described generically rather than guessed at.

Conservative legend:

- `yes` = generally safe to disable for most personal devices
- `if-unused` = usually safe only if you do not use that feature
- `no` = maybe disableable in some setups, but I would not broadly recommend it
- `NO` = critical: core usage, measured CPU loops, repeated crashes, or dependency failures. The specific description explains the evidence; it is not synonymous with bootloop risk
- `KEEP` = keep enabled by default because tests found dependency errors or functionality loss; not a bootloop claim. Manual disabling remains available in the app.

| #   | daemon                        | description                                                                                  | safe_to_disable_for_most_users |
| --- | ----------------------------- | -------------------------------------------------------------------------------------------- | ------------------------------ |
| 1   | ABDatabaseDoctor               | Contacts database repair helper                                                              | yes                            |
| 2   | abm-helper                     | Apple Business Manager                                                                       | yes                            |
| 3   | absd                           | Absinthe/NAC authentication dependency; restoring it removed -44303 initialization errors | NO |
| 4   | accessibility.axassetsd        | Accessibility asset downloads, such as voices                                                | if-unused                      |
| 5   | accessibility.axremoted        | Remote accessibility services                                                                | if-unused                      |
| 6   | accessibility.heard            | Disables Background white noise and hearing aids                                             | NO                             |
| 7   | accessibility.motiontrackingd  | Accessibility motion/head-tracking support                                                   | if-unused                      |
| 8   | accessoryupdaterd              | Firmware updates for Apple/supported accessories                                             | if-unused                      |
| 9   | accountsd                      | System accounts database and access management                                               | NO                             |
| 10  | activityawardsd                | Fitness/Activity awards processing                                                           | if-unused                      |
| 11  | adid                           | apple id account login                                                                       | NO                             |
| 12  | AdminLite                      | system watchdog daemon                                                                       | NO                             |
| 13  | afcd                           | Apple File Conduit for USB file access and Finder/iTunes file sharing                        | yes                            |
| 14  | aggregated                     | System analytics aggregation                                                                 | yes                            |
| 15  | aggregated.addaily             | Advertising-related daily aggregation task                                                   | yes                            |
| 16  | akd                            | apple id account login                                                                       | NO                             |
| 17  | amsaccountsd                   | Apple Media Services account handling                                                        | no                             |
| 18 | amsengagementd | Apple Media Services UI engagement/cache/link endpoints; second new free-app download passed with this job off | if-unused |
| 19  | analyticsd                     | System analytics and diagnostics collection                                                  | yes                            |
| 20  | and                            | Private Apple daemon, unclear public role                                                    | no                             |
| 21  | announced                      | Announce Notifications / Announce Messages support                                           | if-unused                      |
| 22  | anomalydetectiond              | celerometer and gyroscope data for Crash Detection and Fall Detection                        | yes                            |
| 23  | ap.adprivacyd                  | Advertising privacy policy enforcement                                                       | yes                            |
| 24  | ap.promotedcontentd            | Promoted content and App Store recommendations/ads                                           | yes                            |
| 25  | appleaccountd                  | Apple Account management and sync                                                            | NO                             |
| 26  | applecamerad                   | Core camera hardware interface                                                               | no                             |
| 27  | AppleCredentialManagerDaemon   | Apple credential management service                                                          | no                             |
| 28  | AppSSODaemon                   | Single Sign-On service, mainly enterprise                                                    | if-unused                      |
| 29  | appstorecomponentsd            | App Store support components                                                                 | no                             |
| 30  | appstored                      | Core App Store download/update service                                                       | no                             |
| 31  | apsd                           | Apple Push Notification service daemon                                                       | NO                             |
| 32  | arkitd                         | ARKit service for augmented-reality apps                                                     | if-unused                      |
| 33  | asd                            | Private Apple daemon; public role not clearly documented                                     | no                             |
| 34  | askpermissiond                 | Family Sharing Ask to Buy approvals                                                          | if-unused                      |
| 35  | AssetCacheLocatorService       | Finds local Apple caching servers on the network                                             | yes                            |
| 36  | assetsd                        | Photos asset library , disabling disables photos and screenshots vieweing in photos          | NO                             |
| 37  | assetsd.nebulad                | Photos/iCloud Photos asset sync helper                                                       | if-unused                      |
| 38  | assistant_cdmd                 | Siri assistant data management                                                               | if-unused                      |
| 39  | atc                            | Legacy Finder/iTunes sync helper                                                             | yes                            |
| 40  | AuthenticationServicesCore     | AuthenticationServices XPC service for sign-in/passkey flows                                 | no                             |
| 41  | avatarsd                       | Memoji/avatar rendering and sync                                                             | if-unused                      |
| 42  | avconferenced                  | Audio/video conferencing service                                                             | if-unused                      |
| 43  | backboardd                     | Core iOS input, display, and event-routing service                                           | NO                             |
| 44  | backgroundassets.user          | Background asset downloads for apps/system content                                           | no                             |
| 45  | BarcodeSupport                 | Barcode support service                                                                      | if-unused                      |
| 46  | batteryintelligenced           | Battery intelligence and optimized charging support                                          | yes                            |
| 47  | betaenrollmentd                | Beta enrollment management                                                                   | yes                            |
| 48 | biomed | Biome stream access; disabled state produced SpringBoard/keyboard metadata and lockfile failures | NO |
| 49  | biomesyncd                     | Biome sync service                                                                           | no                             |
| 50  | biometrickitd.mesa             | Biometric hardware interface for Touch ID / Face ID                                          | no                             |
| 51  | bird                           | iCloud Drive / CloudDocs sync daemon                                                         | if-unused                      |
| 52  | BlueTool                       | Bluetooth controller bring-up and management                                                 | no                             |
| 53  | bluetoothd                     | Core Bluetooth stack daemon                                                                  | no                             |
| 54  | bluetoothuiservice             | Bluetooth pairing/UI support                                                                 | if-unused                      |
| 55  | bluetoothuserd                 | User-level Bluetooth support                                                                 | if-unused                      |
| 56  | bookassetd                     | Apple Books asset management                                                                 | if-unused                      |
| 57  | bookdatastored                 | Apple Books data storage and sync                                                            | if-unused                      |
| 58  | bootpd                         | BOOTP/DHCP service used for tethering or USB networking                                      | no                             |
| 59  | BTServer.avrcp                 | Bluetooth media control profile support, Prevents pause/play on the headphones if disabled   | NO                             |
| 60  | BTServer.le                    | Bluetooth Low Energy support, saves power                                                    | NO                             |
| 61  | BTServer.map                   | Bluetooth Message Access Profile support                                                     | if-unused                      |
| 62  | BTServer.pbap                  | Bluetooth Phone Book Access Profile support                                                  | if-unused                      |
| 63  | businessservicesd              | Apple business-related services; public role unclear                                         | no                             |
| 64  | cache_delete                   | Storage cleanup and cache purge service                                                      | no                             |
| 65  | calaccessd                     | Calendar database access control                                                             | no                             |
| 66  | CallHistorySyncHelper          | iCloud sync helper for call history                                                          | if-unused                      |
| 67  | captiveagent                   | Captive portal detection for public Wi‑Fi                                                    | if-unused                      |
| 68  | carkitd                        | CarPlay core daemon                                                                          | if-unused                      |
| 69  | CarPlayApp                     | CarPlay UI process                                                                           | if-unused                      |
| 70  | cdpd                           | Core Data Protection; member of tested account sign-in repair set, individual causality unisolated | NO |
| 71  | certui.relay                   | Certificate trust prompt relay                                                               | no                             |
| 72  | cfnetwork.AuthBrokerAgent      | Network authentication broker                                                                | NO                             |
| 73  | cfnetwork.cfnetworkagent       | Core networking agent                                                                        | NO                             |
| 74  | cfprefsd.xpc.daemon            | CoreFoundation preferences daemon                                                            | NO                             |
| 75  | cfprefsd.xpc.daemon.system     | System-level preferences daemon                                                              | NO                             |
| 76  | checkerboardd                  | Recovery/restore or diagnostic UI service                                                    | no                             |
| 77  | chronod                        | Widget timelines and scheduled widget updates                                                | if-unused                      |
| 78  | ckdiscretionaryd               | CloudKit background transfer service                                                         | if-unused                      |
| 79  | ClarityBoard                   | Assistive Access / simplified accessibility UI                                               | if-unused                      |
| 80  | ClipServices.clipserviced      | App Clips management service                                                                 | if-unused                      |
| 81  | cloudd                         | CloudKit syncing daemon                                                                      | if-unused                      |
| 82  | cloudpaird                     | iCloud-based pairing info sync                                                               | if-unused                      |
| 83  | cloudphotod                    | iCloud Photos background sync                                                                | if-unused                      |
| 84  | CloudSettingsSyncAgent         | iCloud settings sync agent                                                                   | if-unused                      |
| 85  | cmfsyncagent                   | Private Apple sync agent, unclear public role                                                | no                             |
| 86  | commandandcontrol              | Voice Control accessibility daemon                                                           | if-unused                      |
| 87  | CommCenter                     | Core cellular and telephony daemon                                                           | NO                             |
| 88  | CommCenter-noncellular         | Network-service variant for non-cellular devices                                             | no                             |
| 89  | CommCenterMobileHelper         | Cellular helper for carrier/config tasks                                                     | NO                             |
| 90  | CommCenterRootHelper           | Privileged cellular configuration helper                                                     | NO                             |
| 91  | companion_proxy                | Apple Watch companion proxy service                                                          | if-unused                      |
| 92  | companionauthd                 | Apple Watch authentication and pairing support                                               | if-unused                      |
| 93  | configd                        | System Configuration and network configuration                                               | NO                             |
| 94  | contacts.donation-agent        | Contacts suggestions donation agent                                                          | yes                            |
| 95  | contactsd                      | Contacts database daemon                                                                     | no                             |
| 96  | containermanagerd              | App sandbox and container manager                                                            | NO                             |
| 97  | containermanagerd.system       | System-level container manager                                                               | NO                             |
| 98  | contextstored                  | CoreDuet context store for scheduling; Off reproduced Photos upload rejection from failed context reads, zero Wi-Fi quality and inadequate data budget | NO |
| 99  | coordinated                    | File/document coordination service                                                           | no                             |
| 100 | CoreAuthentication.daemon      | Local authentication service for biometric/passcode prompts                                  | NO                             |
| 101 | corecaptured                   | Low-level diagnostic capture service                                                         | yes                            |
| 102 | coredatad                      | Core Data support daemon used by system apps/frameworks                                      | no                             |
| 103 | coreduetd                      | On-device usage learning and prediction service                                              | no                             |
| 104 | coreidvd                       | Digital ID / Wallet identity verification support                                            | if-unused                      |
| 105 | corerepaird                    | Repair status / parts validation service                                                     | if-unused                      |
| 106 | coreservices.useractivityd     | NSUserActivity and Handoff support                                                           | if-unused                      |
| 107 | corespeechd                    | Core speech service for Siri / voice trigger support                                         | if-unused                      |
| 108 | corespotlightservice           | Core Spotlight indexing/search support                                                       | yes                            |
| 109 | countryd                       | Country/region determination service                                                         | no                             |
| 110 | ctkd                           | CryptoTokenKit daemon for smart cards/token credentials                                      | if-unused                      |
| 111 | dasd                           | Duet Activity Scheduler for background tasks                                                 | NO                             |
| 112 | dataaccess.dataaccessd         | Exchange, CalDAV, and CardDAV sync service                                                   | if-unused                      |
| 113 | DataDetectorsSourceAccess      | Data Detectors source-access service                                                         | no                             |
| 114 | datastored                     | Private Apple data-storage service, unclear public role                                      | no                             |
| 115 | deferredmediad                 | Deferred camera/photo processing service                                                     | no                             |
| 116 | deleted_helper                 | Helper for cache deletion and storage cleanup                                                | no                             |
| 117 | DesktopServicesHelper          | CoreServices file/metadata helper                                                            | no                             |
| 118 | device-o-matic                 | Private device/hardware helper service                                                       | no                             |
| 119 | deviceaccessd                  | Device-access / permission-mediation service                                                 | no                             |
| 120 | devicecheckd                   | DeviceCheck / App Attest support service                                                     | no                             |
| 121 | devicedatasetresd              | Private Apple daemon, unclear public role                                                    | no                             |
| 122 | devicemanagementclient.mdmd    | Managed device-management client component                                                   | if-unused                      |
| 123 | devicemanagementclient.teslad  | Managed enrollment / device-management helper                                                | if-unused                      |
| 124 | dhcp6d                         | DHCPv6 client daemon                                                                         | NO                             |
| 125 | diagnosticd                    | System diagnostics processing                                                                | yes                            |
| 126 | diagnosticextensionsd          | Diagnostic extension collection                                                              | yes                            |
| 127 | dietapplecamerad               | Lightweight camera service used by specific pipelines                                        | no                             |
| 128 | dietappleh13camerad            | Hardware-specific lightweight camera daemon                                                  | no                             |
| 129 | diskarbitrationd               | Disk arbitration and mount management                                                        | no                             |
| 130 | diskimagesiod                  | Disk image mount support service                                                             | NO                             |
| 131 | diskimagesiod.ram              | RAM disk image support service                                                               | yes                            |
| 132 | distnoted.xpc.daemon           | Distributed notifications service                                                            | NO                             |
| 133 | dmd                            | Declarative Device Management daemon                                                         | if-unused                      |
| 134 | DMHelper                       | Device-management helper                                                                     | if-unused                      |
| 135 | donotdisturbd                  | Do Not Disturb / Focus management                                                            | NO                             |
| 136 | dprivacyd                      | Differential Privacy telemetry service                                                       | yes                            |
| 137 | DragUI.druid                   | Drag-and-drop UI support                                                                     | NO                             |
| 138 | driverkitd                     | DriverKit user-space driver host service                                                     | no                             |
| 139 | dt.automationmode-writer       | Developer Automation Mode support task                                                       | yes                            |
| 140 | dt.AutomationModeUI            | Developer Automation Mode UI service                                                         | yes                            |
| 141 | dt.fetchsymbolsd               | Developer symbol fetch service                                                               | yes                            |
| 142 | dt.previewsd                   | Developer preview support service                                                            | yes                            |
| 143 | dt.remotepairingdeviced        | Developer wireless pairing daemon                                                            | yes                            |
| 144 | duetexpertd                    | Duet/Siri suggestions expert service                                                         | if-unused                      |
| 145 | email.maild                    | Apple Mail background sync/fetch                                                             | if-unused                      |
| 146 | EscrowSecurityAlert            | Security escrow alert service                                                                | yes                            |
| 147 | exchangesyncd                  | Microsoft Exchange sync daemon                                                               | if-unused                      |
| 148 | facemetricsd                   | Face ID metrics / attention support                                                          | no                             |
| 149 | factory.NFQRCoded              | Factory/diagnostic QR or NFC code support                                                    | yes                            |
| 150 | fairplayd.H2                   | FairPlay DRM daemon, apps installed from the app store dont launch if disabled               | NO                             |
| 151 | fairplaydeviceidentityd        | FairPlay device identity support, apps installed from the app store dont launch if disabled  | NO                             |
| 152 | familycircled                  | Family Sharing management daemon                                                             | if-unused                      |
| 153 | FamilyControlsAgent            | Family Controls / Screen Time family enforcement                                             | if-unused                      |
| 154 | familynotificationd            | Family Sharing notifications daemon                                                          | if-unused                      |
| 155 | fdrhelper                      | Factory restore / calibration key helper                                                     | no                             |
| 156 | FileCoordination               | File coordination service                                                                    | no                             |
| 157 | FileProvider                   | Files app and third-party file-provider support                                              | no                             |
| 158 | filesystems.apfs_iosd          | APFS filesystem management daemon                                                            | NO                             |
| 159 | filesystems.livefileproviderd  | Live File Provider filesystem support                                                        | no                             |
| 160 | filesystems.smbclientd         | SMB client daemon for network shares                                                         | if-unused                      |
| 161 | filesystems.userfs_helper      | User-space filesystem helper                                                                 | no                             |
| 162 | filesystems.userfsd            | User-space filesystem daemon                                                                 | no                             |
| 163 | financed                       | Apple financial services support                                                             | if-unused                      |
| 164 | fitcore                        | Fitness framework core service                                                               | if-unused                      |
| 165 | fitcore.session                | Fitness active-session support service                                                       | if-unused                      |
| 166 | fitnesscoachingd               | Fitness coaching notifications                                                               | if-unused                      |
| 167 | followupd                      | CoreFollowUp account requests; member of tested repair set, individual causality unisolated | NO |
| 168 | fontservicesd                  | System font management and font downloads                                                    | no                             |
| 169 | fs_task_scheduler              | Filesystem maintenance scheduler                                                             | no                             |
| 170 | fseventsd                      | Filesystem events daemon                                                                     | NO                             |
| 171 | ftp-proxy-embedded             | Legacy FTP proxy service                                                                     | yes                            |
| 172 | fullkeyboardaccess             | Accessibility Full Keyboard Access support                                                   | if-unused                      |
| 173 | fusiond                        | Private Apple daemon, unclear public role                                                    | no                             |
| 174 | GameController.gamecontrollerd | Game controller framework daemon                                                             | if-unused                      |
| 175 | geod                           | GeoServices daemon for Maps/geocoding                                                        | no                             |
| 176 | gpsd                           | GPS hardware daemon                                                                          | no                             |
| 177 | griddatad                      | Private Apple grid/layout data service                                                       | no                             |
| 178 | GSSCred                        | Kerberos credential daemon                                                                   | if-unused                      |
| 179 | handwritingd                   | Handwriting recognition service                                                              | if-unused                      |
| 180 | hangreporter                   | Reports app/system hangs                                                                     | yes                            |
| 181 | hangtracerd                    | Collects traces for app/system hangs                                                         | yes                            |
| 182 | healthappd                     | Health app support daemon                                                                    | if-unused                      |
| 183 | healthrecordsd                 | Health Records sync/storage service                                                          | if-unused                      |
| 184 | icloud.findmydeviced           | Find My device support daemon                                                                | if-unused                      |
| 185 | icloud.fmfd                    | Find My People support daemon                                                                | if-unused                      |
| 186 | icloud.fmflocatord             | Find My location reporting daemon                                                            | if-unused                      |
| 187 | icloud.searchpartyd            | Find My network / AirTag crowd-location daemon                                               | if-unused                      |
| 188 | icloudsubscriptionoptimizerd   | iCloud subscription/storage upsell service                                                   | yes                            |
| 189 | iconservices.iconservicesagent | App icon rendering and cache service                                                         | no                             |
| 190 | idamd                          | Private Apple identity/auth daemon                                                           | no                             |
| 191 | idcredd                        | Identity credential service for digital IDs                                                  | if-unused                      |
| 192 | identityservicesd              | Identity Services; member of tested account repair set and earlier nehelper dependency recovery | NO |
| 193 | idscredentialsagent            | Identity Services credential agent                                                           | no                             |
| 194 | idsremoteurlconnectionagent    | Identity Services network agent                                                              | no                             |
| 195 | imagent                        | Messages backend including carrier SMS; restore plus respring recovered verification SMS | NO |
| 196 | imautomatichistorydeletionagent | Automatic message-history deletion support                                                   | yes                            |
| 197 | imtransferagent                | Transfers iMessage attachments/media                                                         | no                             |
| 198 | inboxupdaterd                  | Inbox update helper; public role not well documented                                         | no                             |
| 199 | ind                            | Private Apple daemon, unclear public role                                                    | no                             |
| 200 | installcoordination_proxy      | Proxy helper for install coordination                                                        | no                             |
| 201 | installcoordinationd           | Coordinates app installs and updates                                                         | no                             |
| 202 | intelligenceplatformd          | On-device intelligence platform for Siri/predictions                                         | if-unused                      |
| 203 | IOAccelMemoryInfoCollector     | GPU/graphics memory diagnostics collector                                                    | yes                            |
| 204 | iomfb_bics_daemon              | Display subsystem calibration/framebuffer helper                                             | no                             |
| 205 | iomfb_fdr_loader               | Factory display calibration loader                                                           | no                             |
| 206 | iosdiagnosticsd                | Apple diagnostics/support daemon                                                             | yes                            |
| 207 | ioupsd                         | Private power-related daemon, unclear public role                                            | no                             |
| 208 | itunesstored                   | Store services daemon for media purchases/downloads                                          | if-unused                      |
| 209 | jetsamproperties.D21           | Memory-pressure and Jetsam configuration                                                     | NO                             |
| 210 | jetsamproperties.D211          | Hardware-specific Jetsam configuration                                                       | NO                             |
| 211 | keychainsharingmessagingd      | Keychain sharing / iCloud Keychain messaging support                                         | if-unused                      |
| 212 | knowledgeconstructiond         | Builds knowledge data for Siri/search                                                        | yes                            |
| 213 | languageassetd                 | Downloads language assets such as dictionaries/voices                                        | if-unused                      |
| 214 | linkd                          | Linking service for intents/deep links/app routing                                           | no                             |
| 215 | liveactivitiesd | Live Activities and tested Lock Screen/Notification Center playback panel; requires PosterBoard for clear rendering in this profile | if-unused |
| 216 | localizationswitcherd          | Localization/language-switch helper                                                          | if-unused                      |
| 217 | locationd                      | Core Location daemon                                                                         | no                             |
| 218 | locationpushd                  | Location-triggered push-notification support                                                 | if-unused                      |
| 219 | logd                           | Unified logging daemon                                                                       | no                             |
| 220 | logd_helper                    | Helper for unified logging                                                                   | no                             |
| 221 | lsd                            | LaunchServices daemon for app registration/launching                                         | NO                             |
| 222 | lsd.system                     | System-level LaunchServices daemon                                                           | NO                             |
| 223 | lskdd                          | Private Apple daemon, unclear public role                                                    | NO                             |
| 224 | managedconfiguration.passcodenagd | Managed passcode compliance prompt service                                                   | if-unused                      |
| 225 | managedconfiguration.profiled  | Configuration profile and managed-policy daemon                                              | if-unused                      |
| 226 | ManagedSettingsAgent           | Managed Settings / Screen Time enforcement                                                   | if-unused                      |
| 227 | maps.destinationd              | Maps destination suggestions service                                                         | if-unused                      |
| 228 | maps.geocorrectiond            | Maps location-correction / feedback daemon                                                   | if-unused                      |
| 229 | maps.pushdaemon                | Maps notification daemon                                                                     | if-unused                      |
| 230 | matd                           | Private measurement/telemetry daemon                                                         | no                             |
| 231 | mDNSResponder                  | Bonjour and Multicast DNS daemon                                                             | NO                             |
| 232 | mDNSResponderHelper            | Helper for Bonjour/mDNS networking                                                           | NO                             |
| 233 | mdt                            | Private Apple daemon, unclear public role                                                    | no                             |
| 234 | mediaanalysisd                 | On-device media analysis for Photos                                                          | yes                            |
| 235 | mediaanalysisd.service         | XPC service for media analysis tasks                                                         | yes                            |
| 236 | mediaartworkd                  | Downloads/manages album art and related artwork                                              | if-unused                      |
| 237 | medialibraryd                  | Media library database daemon                                                                | if-unused                      |
| 238 | mediaparserd                   | Parses media files and containers                                                            | no                             |
| 239 | mediaremoted                   | Remote media-control daemon                                                                  | no                             |
| 240 | mediaserverd                   | Core audio/video playback service                                                            | no                             |
| 241 | mediasetupd                    | Media device setup support, such as HomePod/Apple TV                                         | if-unused                      |
| 242 | mediastream.mstreamd           | Shared Photo Streams / related media stream service                                          | if-unused                      |
| 243 | memory-maintenance             | Memory-pressure housekeeping task                                                            | no                             |
| 244 | merchantd                      | Apple Pay merchant-session / transaction support                                             | if-unused                      |
| 245 | metrickitd                     | MetricKit performance metrics collection                                                     | yes                            |
| 246 | misagent                       | Provisioning profile and signing support agent, disabling will cause self signed to not open | NO                             |
| 247 | mlmodelingd                    | Downloads/manages machine-learning models                                                    | yes                            |
| 248 | mlruntimed                     | Core ML runtime daemon                                                                       | no                             |
| 249 | mobile.assertion_agent         | Manages process assertions and background-execution claims                                   | NO                             |
| 250 | mobile.cache_delete_app_container_caches | Clears app container caches                                                                  | no                             |
| 251 | mobile.cache_delete_daily      | Daily cache-cleanup task                                                                     | no                             |
| 252 | mobile.cache_delete_mobile_backup | Cache cleanup related to device backups                                                      | yes                            |
| 253 | mobile.heartbeat               | System heartbeat/watchdog-related service                                                    | no                             |
| 254 | mobile.house_arrest            | USB file-sharing service for app containers                                                  | yes                            |
| 255 | mobile.insecure_notification_proxy | USB communication with a computer                                                            | if-unused                      |
| 256 | mobile.installd                | Core app installation daemon                                                                 | no                             |
| 257 | mobile.keybagd                 | Keybag and data-protection key management                                                    | NO                             |
| 258 | mobile.lockdown | USB pairing/trust and host communication; paired USB tools failed when off and worked after restoration | NO |
| 259 | mobile.MCInstall               | Mobile configuration install helper                                                          | if-unused                      |
| 260 | mobile.notification_proxy      | Lockdown notification proxy for host communication                                           | if-unused                      |
| 261 | mobile.storage_mounter         | Mount service for developer/external storage images                                          | if-unused                      |
| 262 | mobile.storage_mounter_proxy   | Proxy for storage mount operations                                                           | if-unused                      |
| 263 | mobile.usermanagerd            | DEVICE WILL BOOT LOOP IF TURNED OFF                                                          | NO                             |
| 264 | mobile_installation_proxy      | Proxy service for app installation operations                                                | no                             |
| 265 | mobileactivationd              | Activation status and server-communication daemon                                            | NO                             |
| 266 | mobileassetd                   | Downloads Apple mobile assets such as trust/language files                                   | no                             |
| 267 | mobilebackup2                  | Finder/iTunes local backup service                                                           | if-unused                      |
| 268 | mobilecheckpoint.checkpointd   | Update/checkpoint validation daemon                                                          | no                             |
| 269 | MobileFileIntegrity            | AMFI code-signing enforcement                                                                | NO                             |
| 270 | mobilegestalt.xpc              | System capability and hardware-property query service                                        | NO                             |
| 271 | mobilerepaird                  | Parts pairing and repair-status service                                                      | if-unused                      |
| 272 | mobilestoredemod               | Retail demo mode daemon                                                                      | yes                            |
| 273 | mobilestoredemodhelper         | Retail demo mode helper                                                                      | yes                            |
| 274 | mobiletimerd                   | Clock alarms and timers support                                                              | no                             |
| 275 | momentsd                       | Photos Memories/moments curation                                                             | if-unused                      |
| 276 | MTLAssetUpgraderD              | Metal asset/shader upgrade service                                                           | no                             |
| 277 | mtmergeprops                   | Multi-touch calibration/property merge support                                               | no                             |
| 278 | nand.aspcarry                  | Private NAND/flash-storage support task                                                      | no                             |
| 279 | nand_task_scheduler            | Flash-storage maintenance scheduler                                                          | no                             |
| 280 | nanobackupd                    | Apple Watch backup daemon                                                                    | if-unused                      |
| 281 | nanoprefsyncd                  | Apple Watch preference-sync daemon                                                           | if-unused                      |
| 282 | nanoregistryd                  | CAUSES BOOTLOOP IF DISABLED                                                                  | NO                             |
| 283 | nanoregistrylaunchd            | Apple Watch registry launch helper                                                           | if-unused                      |
| 284 | nanotimekitcompaniond          | Apple Watch face/TimeKit companion sync                                                      | if-unused                      |
| 285 | naturallanguaged               | Natural Language framework daemon                                                            | if-unused                      |
| 286 | navd                           | Turn-by-turn navigation service for Maps                                                     | if-unused                      |
| 287 | ndoagent                       | Private Apple daemon, unclear public role                                                    | no                             |
| 288 | neagent-ios                    | Network Extension agent for VPNs/filters                                                     | if-unused                      |
| 289 | nearbyd                        | Nearby interaction and proximity service                                                     | if-unused                      |
| 290 | nehelper-embedded | Network Extension helper service | NO |
| 291 | nesessionmanager               | DISABLING CAUSES MASSIVE POWER DRAW                                                          | NO                             |
| 292 | NetworkLinkConditioner         | Developer network-throttling tool                                                            | yes                            |
| 293 | networkserviceproxy            | Network proxy service used by Private Relay and related features                             | if-unused                      |
| 294 | newsd                          | Apple News background service                                                                | if-unused                      |
| 295 | nexusd                         | Private Apple network-related daemon                                                         | no                             |
| 296 | nfcd                           | NFC hardware daemon                                                                          | if-unused                      |
| 297 | nfrestore                      | NFC firmware/restore support service                                                         | if-unused                      |
| 298 | notifyd                        | Darwin notification center daemon                                                            | NO                             |
| 299 | nsurlsessiond                  | Background transfer daemon for URLSession networking                                         | no                             |
| 300 | online-auth-agent.xpc          | Online authentication helper for sign-in/activation flows                                    | no                             |
| 301 | osanalytics.osanalyticshelper  | OS analytics formatting helper                                                               | yes                            |
| 302 | ospredictiond                  | System prediction daemon                                                                     | yes                            |
| 303 | parsec-fbf                     | Feedback/analytics service for Siri Search/Spotlight                                         | yes                            |
| 304 | parsecd                        | Siri Search / Spotlight web-query service                                                    | if-unused                      |
| 305 | pasteboard.pasted              | System pasteboard/clipboard daemon                                                           | no                             |
| 306 | pcapd                          | Packet-capture daemon for diagnostics                                                        | yes                            |
| 307 | peakpowermanagerd              | Performance and battery-aging management daemon                                              | no                             |
| 308 | peopled                        | People-intelligence daemon for contact suggestions                                           | yes                            |
| 309 | perfdiagsselfenabled           | Performance diagnostics task                                                                 | yes                            |
| 310 | PerfPowerServicesExtended      | Extended power/performance telemetry                                                         | yes                            |
| 311 | pfd                            | Packet Filter / firewall support daemon                                                      | no                             |
| 312 | photoanalysisd                 | Photos analysis daemon for faces/objects/scenes                                              | yes                            |
| 313 | photos.ImageConversionService | Photos sharing/export image conversion; disabling caused Unable to Share, restoring plus respring repaired WhatsApp photo sharing | NO |
| 314 | photos.VideoConversionService | Photos sharing/export video conversion; separate disable/restore plus respring comparison repaired WhatsApp Camera video sharing | NO |
| 315 | pipelined                      | Image/camera processing pipeline daemon                                                      | no                             |
| 316 | pluginkit.pkd | App-extension broker; disabled pkd reproduced SpringBoard widget-discovery CPU loop at about one core | NO |
| 317 | pluginkit.pkreporter           | PlugInKit diagnostics reporter                                                               | yes                            |
| 318 | pointerUI.pointeruid           | Pointer UI daemon for mouse/trackpad support                                                 | if-unused                      |
| 319 | powerd                         | Core power-management daemon                                                                 | NO                             |
| 320 | powerdatad                     | Battery-usage data collection/reporting                                                      | yes                            |
| 321 | powerlogHelperd                | Power logging helper                                                                         | yes                            |
| 322 | PowerUIAgent                   | Power/battery-related UI alerts agent                                                        | no                             |
| 323 | preboardservice                | Pre-boot security / passcode UI service                                                      | NO                             |
| 324 | preboardservice_v2             | Updated pre-boot security UI service                                                         | NO                             |
| 325 | privacyaccountingd             | App Privacy Report accounting service                                                        | yes                            |
| 326 | proactiveeventtrackerd         | Tracks events for proactive Siri suggestions                                                 | yes                            |
| 327 | progressd                      | System progress-reporting daemon                                                             | no                             |
| 328 | protectedcloudstorage.protectedcloudkeysyncing | Protected iCloud encryption-key sync; disabled endpoint caused CloudDocs user-key/zone preparation failures | NO |
| 329 | ProxiedCrashCopier.ProxyingDevice | Copies crash logs from proxied/paired devices                                                | yes                            |
| 330 | proximitycontrold              | Proximity-based handoff/device-interaction service                                           | if-unused                      |
| 331 | ptpcamerad                     | PTP camera access daemon for importing photos                                                | if-unused                      |
| 332 | ptpd                           | PTP daemon used for photo transfer / related sync                                            | if-unused                      |
| 333 | purplebuddy.budd               | Setup Assistant daemon                                                                       | if-unused                      |
| 334 | PurpleReverseProxy             | Reverse proxy used by some USB/device communication flows                                    | if-unused                      |
| 335 | quicklook.ThumbnailsAgent      | Quick Look thumbnail-generation service                                                      | if-unused                      |
| 336 | rapportd                       | CAUSED HIGH CPU USAGE IF DISABLED                                                            | NO                             |
| 337 | recentsd                       | Recent contacts and suggestions daemon                                                       | yes                            |
| 338 | relatived                      | Motion/sensor-relative tracking service                                                      | if-unused                      |
| 339 | remindd                        | Reminders database/sync daemon                                                               | if-unused                      |
| 340 | remoted                        | Apple remote services daemon; public role not fully clear                                    | no                             |
| 341 | RemoteManagementAgent          | Remote-management agent for supervised/managed devices                                       | if-unused                      |
| 342 | remotemanagementd              | Remote-management daemon for supervised/managed devices                                      | if-unused                      |
| 343 | replayd                        | ReplayKit screen-recording/broadcast daemon                                                  | if-unused                      |
| 344 | ReportMemoryException          | Reports out-of-memory events                                                                 | yes                            |
| 345 | RestoreRemoteServices.restoreserviced | Restore support service used by host restore workflows                                       | if-unused                      |
| 346 | retimerd                       | Private Apple hardware daemon, unclear public role                                           | no                             |
| 347 | reversetemplated               | Private Apple daemon, unclear public role                                                    | no                             |
| 348 | revisiond                      | Document revision/versioning support service                                                 | if-unused                      |
| 349 | routined                       | Significant locations and routine-learning daemon                                            | yes                            |
| 350 | rtcreportingd                  | SIP servers for FaceTime and Wi-Fi Calling                                                   | yes                            |
| 351 | runningboardd                  | Process lifecycle and resource-management daemon                                             | NO                             |
| 352 | Safari.History                 | Safari history support service                                                               | if-unused                      |
| 353 | Safari.passwordbreachd         | Safari password-breach checking service                                                      | yes                            |
| 354 | Safari.SafeBrowsing.Service    | Safari fraudulent-website check service                                                      | yes                            |
| 355 | SafariBookmarksSyncAgent       | Safari bookmarks sync agent                                                                  | if-unused                      |
| 356 | safarifetcherd                 | Safari Reading List offline fetcher                                                          | if-unused                      |
| 357 | safetyalertsd                  | Safety alerts service, including Emergency SOS/crash alerts                                  | NO                             |
| 358 | SCHelper                       | SystemConfiguration helper                                                                   | no                             |
| 359 | screensharingserver            | Screen-sharing / remote-screen service                                                       | if-unused                      |
| 360 | ScreenTimeAgent                | Screen Time tracking agent                                                                   | if-unused                      |
| 361 | scrod                          | Accessibility screen-recognition / OCR daemon                                                | if-unused                      |
| 362 | SecureBackupDaemon             | CloudServices secure-backup/key-recovery broker; missing endpoint blocked PCS metadata step, restore/retry completed key sync | NO |
| 363 | security.CircleJoinRequested   | iCloud Keychain trust-circle approval service                                                | if-unused                      |
| 364 | security.cloudkeychainproxy3   | iCloud Keychain proxy daemon                                                                 | if-unused                      |
| 365 | security.cryptexd              | Cryptex and Rapid Security Response management                                               | NO                             |
| 366 | security.swcagent              | Shared Web Credentials agent                                                                 | no                             |
| 367 | securityd                      | Core security and authorization daemon                                                       | NO                             |
| 368 | securityuploadd                | Security telemetry / report upload daemon                                                    | yes                            |
| 369 | seld                           | Secure Element daemon for Apple Pay and related operations                                   | if-unused                      |
| 370 | SensorKitALSHelper             | SensorKit ambient-light helper                                                               | yes                            |
| 371 | sensorkitd                     | SensorKit daemon for approved sensor APIs                                                    | yes                            |
| 372 | SepUpdateTimer                 | Secure Enclave update scheduling task                                                        | no                             |
| 373 | seserviced                     | Secure Element services daemon                                                               | if-unused                      |
| 374 | sharingd                       | disabling disables the Share sheet                                                           | NO                             |
| 375 | shazamd                        | Music-recognition daemon for Shazam integration                                              | if-unused                      |
| 376 | sidecar-relay                  | Sidecar relay service                                                                        | if-unused                      |
| 377 | signpost.signpost_reporter     | Performance signpost reporting task                                                          | yes                            |
| 378 | siriactionsd                   | HIGH CPU USAGE IF DISABLED                                                                   | NO                             |
| 379 | siriinferenced                 | On-device Siri inference daemon                                                              | if-unused                      |
| 380 | siriknowledged                 | Siri knowledge-cache daemon                                                                  | if-unused                      |
| 381 | sirittsd                       | Siri text-to-speech daemon                                                                   | if-unused                      |
| 382 | sleepd                         | CAUSES BOOT LOOP IF DISABLED                                                                 | NO                             |
| 383 | sntpd                          | Simple Network Time Protocol daemon                                                          | no                             |
| 384 | sociallayerd                   | Shared with You / related social-content integration                                         | if-unused                      |
| 385 | softposreaderd                 | Tap to Pay on iPhone / SoftPOS reader service                                                | if-unused                      |
| 386 | sosd                           | Emergency SOS service daemon                                                                 | NO                             |
| 387 | speechmodeltrainingd           | On-device speech personalization/training                                                    | yes                            |
| 388 | spindump                       | Collects spindump diagnostics                                                                | yes                            |
| 389 | splashboardd                   | Launch-screen snapshot and splash-screen management                                          | no                             |
| 390 | sportsd                        | Sports data background service for Apple apps                                                | yes                            |
| 391 | Spotlight.IndexAgent           | Spotlight indexing agent                                                                     | no                             |
| 392 | spotlightknowledged            | Spotlight knowledge integration daemon                                                       | if-unused                      |
| 393 | SpringBoard                    | Primary iOS home screen and app UI manager                                                   | NO                             |
| 394 | StatusKitAgent                 | Focus status sharing agent                                                                   | if-unused                      |
| 395 | storagedatad                   | Storage accounting and iPhone Storage calculations                                           | yes                            |
| 396 | storagekitd                    | Storage framework daemon                                                                     | no                             |
| 397 | storebookkeeperd               | Store-related bookkeeping such as play-state/metadata                                        | if-unused                      |
| 398 | storekitd                      | StoreKit and in-app purchase daemon                                                          | no                             |
| 399 | streaming_zip_conduit          | Streamed archive conduit used by install/host tooling                                        | if-unused                      |
| 400 | subridged                      | Apple Watch bridge service                                                                   | if-unused                      |
| 401 | suggestd                       | Suggestions daemon for Mail/Messages/proactive features                                      | if-unused                      |
| 402 | swcd                           | Shared Web Credentials and associated-domains daemon                                         | no                             |
| 403 | symptomsd                      | Network diagnostics and symptom-detection daemon                                             | yes                            |
| 404 | symptomsd-diag                 | Detailed network diagnostics task                                                            | yes                            |
| 405 | symptomsd.helper               | Helper for network diagnostics and symptom reporting                                         | yes                            |
| 406 | synapse.contentlinkingd        | Cross-app content-linking helper; public role unclear                                        | no                             |
| 407 | SyncAgent                      | Legacy sync agent for media/content sync                                                     | if-unused                      |
| 408 | syncdefaultsd | Synchronized key-value preferences; provides com.apple.kvsd, whose clients logged lookup failures when off | NO |
| 409 | sysdiagnose                    | System diagnostics collection task                                                           | yes                            |
| 410 | sysdiagnose.darwinos           | Low-level Darwin OS diagnostics task                                                         | yes                            |
| 411 | sysdiagnose_helper             | Helper for system diagnostics collection                                                     | yes                            |
| 412 | systemstats.microstackshot_periodic | Periodic microstackshot task for performance/power stats                                     | NO                             |
| 413 | tailspind                      | Collects performance-degradation diagnostics                                                 | yes                            |
| 414 | tccd                           | Privacy permissions daemon                                                                   | NO                             |
| 415 | telephonyutilities.callservicesd | Call services daemon for CallKit, phone calls, and VoIP                                      | NO                             |
| 416 | terminusd                      | Private Apple network-path daemon, unclear public role                                       | no                             |
| 417 | TextInput.kbd                  | Keyboard text-input service                                                                  | NO                             |
| 418 | thermalmonitord                | Thermal monitoring and throttling daemon                                                     | NO                             |
| 419 | ThreadCommissionerService      | Thread-network commissioner service for smart-home accessories                               | if-unused                      |
| 420 | timed                          | System clock and time-maintenance daemon                                                     | NO                             |
| 421 | timesync.audioclocksyncd       | Audio clock-sync service                                                                     | if-unused                      |
| 422 | timezoneupdates.tzd            | Time-zone data update daemon                                                                 | no                             |
| 423 | touchsetupd                    | Device-to-device setup and transfer helper                                                   | if-unused                      |
| 424 | translationd                   | System translation framework daemon                                                          | if-unused                      |
| 425 | transparencyd                  | Apple transparency verification daemon for security features                                 | no                             |
| 426 | transparencyStaticKey          | Static key/support component for transparency services                                       | no                             |
| 427 | triald                         | Feature-flag and experimentation daemon                                                      | yes                            |
| 428 | trustd                         | Certificate trust-evaluation daemon                                                          | NO                             |
| 429 | tvremoted                      | Apple TV Remote support daemon                                                               | if-unused                      |
| 430 | tzlinkd                        | Private time-zone helper daemon                                                              | no                             |
| 431 | uikit.eyedropperd              | UIKit color-picker / eyedropper support                                                      | yes                            |
| 432 | UsageTrackingAgent             | Usage-tracking agent for Screen Time / related analytics                                     | if-unused                      |
| 433 | usb.networking.addNetworkInterface | USB networking interface setup task                                                          | if-unused                      |
| 434 | usbsmartcardreaderd            | USB smart-card reader daemon                                                                 | if-unused                      |
| 435 | UserEventAgent-System          | System user-event scheduling and launch agent                                                | NO                             |
| 436 | videosubscriptionsd            | TV provider / video-subscription single sign-on daemon                                       | if-unused                      |
| 437 | voicemail.vmd                  | Visual Voicemail daemon                                                                      | if-unused                      |
| 438 | voicememod                     | Voice Memos background support daemon                                                        | if-unused                      |
| 439 | VoiceOverTouch                 | VoiceOver touch-interaction support daemon                                                   | if-unused                      |
| 440 | watchdogd                      | System watchdog daemon                                                                       | NO                             |
| 441 | watchlistd                     | TV app watchlist / Up Next sync daemon                                                       | if-unused                      |
| 442 | watchpresenced                 | Apple Watch proximity / presence detection daemon                                            | if-unused                      |
| 443 | wcd                            | WatchConnectivity daemon for Apple Watch apps                                                | if-unused                      |
| 444 | weatherd                       | Weather framework and weather-data service                                                   | if-unused                      |
| 445 | WebBookmarks.webbookmarksd     | Web bookmarks daemon used by Safari and related apps                                         | if-unused                      |
| 446 | webinspectord                  | WebKit remote-inspection daemon                                                              | if-unused                      |
| 447 | webkit.adattributiond          | WebKit ad attribution / Private Click Measurement daemon                                     | yes                            |
| 448 | webkit.webpushd                | WebKit web-push notifications daemon                                                         | if-unused                      |
| 449 | wifi.hostapd                   | Wi‑Fi hotspot access-point daemon for Personal Hotspot                                       | if-unused                      |
| 450 | wifi.wapic                     | WAPI Wi‑Fi security support daemon                                                           | yes                            |
| 451 | wifianalyticsd                 | Wi‑Fi analytics and telemetry daemon                                                         | yes                            |
| 452 | wifid                          | Core Wi‑Fi network-management daemon                                                         | no                             |
| 453 | WiFiFirmwareLoader             | Wi‑Fi firmware loader service                                                                | no                             |
| 454 | wifip2pd                       | Peer-to-peer Wi‑Fi daemon used by AirDrop/AirPlay                                            | if-unused                      |
| 455 | wifivelocityd                  | Wi‑Fi diagnostics and measurement daemon                                                     | yes                            |
| 456 | WirelessRadioManager           | Coordinates wireless radios such as cellular/Wi‑Fi/Bluetooth                                 | no                             |
| 457 | xpc.roleaccountd               | Private Apple XPC/account-role daemon, unclear public role                                   | no                             |
| 458 | accessoryd                     | External Lightning/dock accessory hardware communication daemon                              | if-unused                      |
| 459 | aslmanager                     | Apple System Log file archiving and maintenance daemon                                       | yes                            |
| 460 | assistant_service              | Siri voice helper and intent dispatch service                                                | if-unused                      |
| 461 | assistantd                     | Siri voice assistant core background daemon                                                  | if-unused                      |
| 462 | assistivetouchd                | AssistiveTouch accessibility on-screen pointer and menu service                              | if-unused                      |
| 463 | AuthenticationServicesAgent    | Web and passkey authentication agent                                                         | no                             |
| 464 | awdd                           | Apple Wireless Diagnostics and telemetry daemon                                              | yes                            |
| 465 | backupd                        | Device backup daemon for local and cloud backup jobs                                         | if-unused                      |
| 466 | BarcodeNotificationService     | Camera barcode and QR code detection notification service                                    | if-unused                      |
| 467 | checkerboard                   | Diagnostic and factory setup assistant UI helper                                             | no                             |
| 468 | coresymbolicationd             | Symbolication service for crash logs and stack traces                                        | yes                            |
| 469 | crash_mover                    | Moves crash logs from temporary to persistent diagnostics directory                          | yes                            |
| 470 | crashreportcopymobile          | Copies mobile user crash reports for diagnostics                                             | yes                            |
| 471 | devicedataresetd               | Handles Erase All Content and Settings device wipe workflows                                 | no                             |
| 472 | DumpBasebandCrash              | Extracts cellular baseband modem crash dumps                                                 | yes                            |
| 473 | DumpPanic                      | Extracts kernel panic logs after an unexpected reboot                                        | yes                            |
| 474 | gamed                          | Game Center friends, achievements, and leaderboard daemon                                    | if-unused                      |
| 475 | healthd                        | HealthKit core database and sensor pedometer logging                                         | if-unused                      |
| 476 | homed                          | HomeKit smart home accessory database and coordinator                                        | if-unused                      |
| 477 | iap2d                          | iPod Accessory Protocol v2 daemon for automotive / MFi accessories                           | if-unused                      |
| 478 | iapauthd                       | MFi accessory hardware cryptographic authentication                                          | if-unused                      |
| 479 | iapd                           | Legacy iPod Accessory Protocol daemon                                                        | if-unused                      |
| 480 | itunescloudd                   | Apple Music cloud library and iTunes in the Cloud sync                                       | if-unused                      |
| 481 | midiserver-ios                 | CoreMIDI server for external musical instruments                                             | if-unused                      |
| 482 | mobile.obliteration            | Low-level NAND filesystem wiping task for factory resets                                     | no                             |
| 483 | mobile.softwareupdated         | Background software update installation coordinator                                          | if-unused                      |
| 484 | MobileInternetSharing          | Personal Hotspot tethering network sharing daemon                                            | if-unused                      |
| 485 | OTACrashCopier                 | Copies over-the-air crash reports for telemetry                                              | yes                            |
| 486 | OTATaskingAgent                | Executes remote Apple diagnostic tasks                                                       | yes                            |
| 487 | passd | Wallet/PassKit backend also required by tested App Store free-app confirmation; restore plus respring repaired download/install/launch | NO |
| 488 | printd                         | AirPrint printing spooler daemon                                                             | if-unused                      |
| 489 | racoon                         | IPsec IKE key exchange daemon for enterprise VPNs                                            | if-unused                      |
| 490 | ReportCrash                    | Core system crash reporter daemon                                                            | yes                            |
| 491 | searchd | Spotlight query coordinator; broad live unloading produced Photos retries and sustained CPU load; single-service causality unproven | NO |
| 492 | softwareupdateservicesd        | iOS software update check and background download daemon                                     | if-unused                      |
| 493 | syslogd                        | Core BSD system log socket daemon                                                            | no                             |
| 494 | tipsd                          | Tips app content sync and notification service                                               | yes                            |
| 495 | voiced                         | VoiceOver speech synthesis audio daemon                                                      | if-unused                      |
| 496 | AccessibilityUIServer | Optional accessibility UI host; basic usage tested with it off; no Recommended On classification | if-unused |
| 497 | PosterBoard | Wallpaper/Lock Screen host and tested media-panel rendering dependency; default Off, enable with liveactivitiesd for that feature | if-unused |
| 498 | PaperBoard | Wallpaper-related UI host; not required for tested Lock Screen playback controls | if-unused |
| 499 | ShortcutsUI | Interactive Shortcuts UI host; kept On on the tested phone, freely switchable | if-unused |
| 500 | sharing.airdropui | AirDrop UI host; native Allow AirDrop policy is separately controlled | if-unused |
| 501 | PassbookUISceneService | Wallet interface host; separate from Critical passd needed by tested App Store confirmation | if-unused |
| 502 | livetranscriptionui | Live Captions UI host on supported devices | if-unused |
| 503 | replaykitangel | ReplayKit screen recording/broadcast UI host; tested with replayd and respring | if-unused |

## Device test findings — 2026-10-11

Tested iPhone 8 Plus (iPhone10,2), iOS 16.7.16 **20H392**, Dopamine rootless, Python **3.9.9**. The roughly 64-minute session exercised 321 installed entries from the old 328-entry default-off list; seven names/aliases were absent. Seven installed targets were initially unregistered and had no live behavior to exercise. These results apply to this device and workload.

Two userspace reboots were performed. The first failed to preserve the off state: the launchd plist had mobile:mobile ownership and mode `01204`, and launchd replaced its entries at startup. After backing up the state, correcting it to root:wheel `0644`, and reapplying through launchctl, the second preserved all 321 off targets. The correction/reapplication passed; ownership versus mode causality was not independently isolated.

`disable` plus killing a process did not reliably stop demand restarts. Test unloading used `bootout`, which exposed intermediate CPU problems. The app now saves native overrides without hot-unloading and tells users to restart userspace for full process changes. This avoids promising immediate RAM savings and reduces disruptive transitions.

Duetexpertd sustained roughly one CPU core in an intermediate state; its exact preceding trigger was not identified. Unloading it settled that workload. Later assetsd sustained roughly one core while Photos retried failed Spotlight queries: 5,364 repeated delete-query errors and matching syndication-worker errors occurred in a diagnostic window. Fresh full-off reboot and a short searchd on/off comparison did not reproduce the loop. Searchd is conservatively kept enabled in the revised preset; duetexpertd is not newly classified as unsafe to disable because unloading it resolved rather than caused the observed load.

The post-reboot retained window contained 558,096 records, including 21,430 errors, 215 faults, and three Loss records. Faults included missing FamilyControls services, Biome write/lockfile failures, failed KVS lookup, preferences access fallbacks, CommCenter NECP errors, and mDNSResponder lock errors. Not every error was isolated to a single toggle. No actual panic, watchdog-timeout, EXC_BAD_ACCESS, or SIGSEGV event was found in that reviewed window; two watchdog matches only configured a timeout. A simulated nonfatal diagnosticd report appeared during earlier collection. Disabled crash collectors and lost log records limit the completeness of crash evidence.

User-confirmed unlock, Notes typing, Camera, Photos, and Safari checks passed after the complete off preset survived reboot. Core processes remained present at measured checkpoints; HTTPS returned 200 and Wi-Fi/USB SSH worked. Battery life, overnight activity, calls/SMS, cellular data, notifications, accessories, and all cloud features were not comprehensively tested.

Baseline median physical free RAM was **215.4 MiB** (six samples); the five-minute post-reboot workload median was **121.2 MiB** (29 samples). Free plus speculative was 364.7 versus 177.1 MiB; compressor occupancy 351.6 versus 328.1 MiB. App activity, reboot effects, and collection differed: these are not isolated performance gains. The >600 MiB idle physical-free target was not achieved.

Verified related Contacts stores on this phone: `/var/mobile/Library/AddressBook/AddressBook.sqlitedb` and `AddressBookImages.sqlitedb`. The app shows metadata and paths for contactsd and ABDatabaseDoctor; it does not read contact records or guess private database locations for unrelated services.

The revised 506-entry config has **320 default-off entries** and is mirrored at the app repository root as `daemon.cfg`. Historical raw test files and obsolete script/app copies were removed from this notes directory after these findings were incorporated. Original device-side test/backup files remain under `/var/mobile/Documents/daemon-test-2026-10-11`; they are not distributed with the app.

## Messages recovery and category revision (2026-10-11)

After the broad off profile, Messages appeared empty. Re-enabling imagent and identityservicesd alone exposed repeated identityservicesd aborts and a black Messages launch screen. Live logging showed `IDSGroupAgent registerAgent` asserting `self.interfaceName`, with nehelper/networkd_privileged unavailable. Enabling the actual `com.apple.nehelper` job restored Identity Services; the user confirmed conversations returned. The database passed SQLite quick_check and retained 247 messages and 79 conversations; a snapshot remains on the phone under `/var/mobile/Documents/messages-recovery-2026-10-11`.

Keep imagent, identityservicesd, and nehelper enabled by default as **KEEP**, not capital **NO**. The `nehelper-embedded` plist filename maps to job label `com.apple.nehelper`; both config entries now target On. These are feature/dependency recommendations, manually switchable without critical confirmation. Nehelper also serves other Network Extension clients: disabling it while Identity Services remains enabled caused repeated background crashes. No additional bootloop classification was established by this recovery.

The app's Critical tab retains all installed capital-NO classified services regardless of their toggle state, with disabled entries first. Existing critical classifications remain intact; no service is promoted solely from a guessed risk. Defaults lists all 506 compiled targets, including entries not installed on the current device. Fresh installations need no config import. The RAM label is only a number and MiB; the header uses kernel free_count, which already includes speculative pages.

## Wallpaper host and storage (v1.0.4)

The verified PosterBoard job is `/System/Library/LaunchAngels/com.apple.PosterBoard.plist`, label `com.apple.PosterBoard`, domain `user/501` on this phone (the app discovers the mobile UID). Killing its PID does not save an override. Native disable plus bootout stopped it and most associated poster extensions in the observed window. A userspace reboot preserved the off override and left no PosterBoard/poster-extension processes running. The user confirmed unlock, Home, Notes and Messages work after this reboot. Battery impact and long-term stability remain unmeasured. The app exposes this job under Tools → Background UI and in Services, together with the tested AccessibilityUIServer host. Other untested LaunchAngels remain excluded. The compiled default keeps it enabled for other users; manual disabling remains available, and userspace restart fully applies it. No auto-kill daemon, polling loop, file renaming, or arbitrary app blacklist is installed.

Speculative pages are reclaimable kernel filesystem cache, not a separately disableable SysMain service. Disabling predictive daemons does not eliminate all speculative pages. No useful verified force-compress control was found; no induced memory pressure or forced app eviction is implemented. Core account daemons and siriactionsd retain their existing critical warnings; feature names alone do not establish safe disabling.

The storage driver advertises NVMe SMART capability, but read-only IOCreatePlugInInterfaceForService returned `0xe00002c7` on this device; lifetime writes/endurance/wear could not be obtained. The old capacity section was removed in v1.0.5; current disk health uses on-demand ASP diagnostic reports. No terabyte-write lifetime or rated endurance is invented.

## Audit follow-up and app revision (v1.0.6)

Tools → Background UI now exposes PosterBoard and AccessibilityUIServer only. AccessibilityUIServer is **KEEP / Recommended On**, not capital NO; its builtin target is enabled. It remains freely switchable, including in Services. The tested phone retains its manual off override. Re-enable and restart userspace before using accessibility features. Installation never applies defaults. The config and IPA contain **506 targets: 320 off, 186 on**; biomed, syncdefaultsd and Messages dependencies remain enabled by default.

Back Tap, Speak Screen and the general accessibility setting were verified off across userspace reboot. AccessibilityUIServer used about 0.002% of one core with those settings off, before its host was disabled; its roughly 23 MiB RSS includes shared pages and is not a guaranteed net RAM gain. The user repeated Home, unlock, Notes, Camera and Messages checks successfully after disabling it and rebooting. All accessibility features and actual iMessage delivery were not independently tested. Disabling this host produces expected unavailable-host endpoint messages.

biomed and syncdefaultsd were restored on the phone. Final reviewed errors no longer explicitly named their missing Biome/KVS endpoints. Restoring contextstored, duetexpertd and coreduetd did not eliminate redacted context failures, so all three were returned off. Remaining CoreDuet, graphics, preferences and account errors are unresolved; no claim of error-free operation is justified. Backboardd averaged 12.46% of one core on Home and 0.514% locked in the final samples. Pressure remained Normal, with no observed panic/watchdog reboot/thermal emergency in the reviewed window. Privacy redactions, three lost-log records and disabled crash reporters limit these findings. No battery-life or permanent CPU improvement was established.

Animation controls and their helper were removed from the app. The independently installed animation tweak is left active and managed outside this app. RAM remains memory-only. Disk write counters use grouped raw numbers and can be tapped for their meaning and conversion status. GB conversion remains unavailable: the ASP snapshot and installed diagnostic parser did not establish bytes per count; page size or generic NVMe units cannot substitute for that evidence. No guessed conversion is shipped.

The unsupported absd Contacts description and related database links were removed. Its actual plist exports com.apple.absinthed and com.apple.absd; the broader private role remains undocumented here. Verified Contacts database links remain for contactsd and ABDatabaseDoctor.

## Current revision — 2026-10-12 / v1.0.8

Current config: 512 targets, 325 off and 187 on. Capital `NO` is also used in the config for critical enabled targets. Pkd, nehelper (plus nehelper-embedded alias), searchd, biomed, syncdefaultsd and mobile.lockdown are Critical; this supersedes their earlier KEEP/recommendation classification above. Searchd's isolated CPU causality remains unproven. Imagent and identityservicesd remain optional Messages recommendations. AccessibilityUIServer is optional, not Recommended On or Critical; its builtin target stays On and the user's manual Off choice remains available.

Pkd's disabled state was isolated on this iPhone 8 Plus/iOS 16.7.16: SpringBoard used 5.153 CPU seconds in 5 seconds with repeated widget-extension discovery. Enable/load plus respring reduced settled usage to 0.209 seconds over 15 seconds. Disabling/unloading pkd and starting SpringBoard again reproduced the same stacks (11.434 CPU seconds over 10 seconds). Restoring it and respringing yielded 0.024 seconds over 15.01 seconds and thermal pressure 0. This was an additional manual disable, not an off builtin. Other app clients can retain stale extension connections until userspace restart: the missing WhatsApp/Save to Files Share Sheet entries returned after such a restart with the optional UI hosts still off.

Overrides is now a separate tab, replacing Config. Import/apply/export is under Tools. Descriptions are visible in collapsed rows; warnings, paths and file metadata stay expandable. The tested optional catalog includes PosterBoard, PaperBoard, AccessibilityUIServer, LiveTranscriptionUI, AirDropUI, PassbookUISceneService, ShortcutsUI, ReplayKitAngel/replayd, chronod and liveactivitiesd. Individual switches immediately save and verify overrides and load/unload those jobs; general daemon batches still require userspace restart. InputUI, OverlayUI, ClockAngel and MediaRemoteUI remain excluded.

PaperBoard exports com.apple.paperboard.services and is wallpaper-related; no broader private role is claimed. Disabling AirDropUI, PassbookUISceneService, LiveTranscriptionUI and PaperBoard followed by respring/userspace restart preserved user-tested Camera photo/video capture and the restored normal Share Sheet. A 10-second Camera-active sample used 0.348 SpringBoard CPU seconds with thermal pressure 0. All new hosts were initially idle with no process to reclaim; no net RAM/battery gain is established.

AirDrop's DiscoverableMode was already Off, which only blocked receiving. A feature-entitled client set native MCFeatureAirDropAllowed/allowAirDrop to false; isAirDropAllowed returned false. Ordinary unentitled Python was rejected by profiled with the exact required feature-setting entitlement and made no policy change. The app's on-demand signed helper changes/verifies only that feature. Its wrapper restores profiled's prior override and loaded state. Sharingd stays on for sharing; no profile or direct policy-database edit is used.

ReplayKit is screen recording/broadcasting, separate from Camera recording. The user rarely uses it and wants both ReplayKitAngel and replayd switchable and normally Off; enable both when needed. ShortcutsUI remains On and independently switchable. The WhatsApp Open Meta AI Auto Shortcut still produced 4097 through linkd after userspace restart and restoring the four new optional hosts. Its core brokers remained enabled. The user verified Apple’s Set Flashlight action works; the remaining WhatsApp failure has not been traced to a disabled service.

## Feature groups and final verification — v1.0.9

Overrides has 13 groups covering 40 installed job labels. On enables listed feature jobs and disabled prerequisites; Off preserves Critical and shared prerequisite-only services. Shared services appear in several groups but have one underlying override. Broader account/sync/store groups save overrides and need userspace restart; sign-in, cloud sync, purchases and FaceTime calls were not tested. These are checked roles/known prerequisites, not a complete OS dependency map.

ReplayKitAngel/replayd restoration initially left SpringBoard’s ReplayKit client uninitialized (isAvailable=1, isInitialized=0, 4099). Starting replayd alone did not repair it. Respring made initialization succeed and screen recording save to Photos. With the eight unrelated optional jobs off again and another respring, the user confirmed recording, Save to Files and WhatsApp sharing all work. Both recording jobs were then disabled as requested. Re-enable both and respring before recording. Apple’s Set Flashlight shortcut works; the unused WhatsApp Meta AI action still fails and is not established as a missing-daemon issue.

Installed v1.0.8 preserved the helper entitlement; native AirDrop read/on/off verified policy 0/1/0 and restored profiled disabled/unloaded each time. The user confirmed Overrides, visible descriptions and the Tools import file picker. A final SpringBoard sample with optional/recording hosts off used 0.004 CPU seconds over 5 seconds (0.08%), thermal pressure 0. This is CPU evidence, not proof of a RAM/battery gain. Widget-update jobs are off, but a universal widget disable is not provided: legacy widget paths can remain and pkd must stay enabled.

Deployment: v1.0.9 / build 10, source `b40f9cbccb871b0e07b6922b8275275d143236d3`, Actions run `38213950882` succeeded (19 Python tests, Swift memory/group policy checks, iOS build and packaging). IPA SHA256 `fa092542663a7dcfe516af1c481947f29a8728d3f4a725fcb912fcb79213e088` matched the release digest. TrollStore Lite install returned 0 and installed Info.plist matches this version/revision; uiopen succeeded. Phone state preserves pkd/Shortcuts On, recording Off, and diagnosticd/profiled Off. Original 336 override values were unchanged by optional-host testing; six explicit host entries were added. App repository changes were pushed; reference notes remain local.

Correction: although v1.0.9 installed and uiopen returned success, the user reported launch crashes. A duplicate com.apple.FileProvider description key caused Swift dictionary initialization to trap. v1.0.10 removes that duplicate and adds Python uniqueness plus Swift dictionary-initialization checks. Services/Critical now consolidate Search, numeric available MiB and theme control into one row, with the filter below; the separate top bar and pinned category headings are removed. Device launch verification is required after installation.

Deployment v1.0.10 / build 11: source ac933c355c2864225afc0975c56ad9f0051f7893, successful Actions run 38215041614 (20 Python tests, Swift memory/group/dictionary checks and iOS build). Release/download SHA256 7eaf0738b024cfb8d364da29dcfdd59add524e13475fb77f892406f2f25ddd19 matched. TrollStore installation returned 0 and installed Info.plist matched. Unlike v1.0.9, the launched process remained present across repeated checks. The user reported the compact layout and recording group work. Recording after group On/respring passed, but the final phone check still found both jobs enabled. A follow-up Off-button check was requested; root restored both jobs Off with verified native unloads to leave the requested normal state. The last UI Off-button outcome remains pending. Account/sync functionality remains untested. Diagnosticd, ReportCrash and profiled were restored disabled/unloaded after launch investigation.

## Apple Account / SMS repair and subpages — v1.0.13

Overrides now has one subpage per feature. The user confirmed navigation and Back on Screen Recording, AirDrop and Apple Account in installed v1.0.12.

Settings was stuck at Apple ID Next: com.apple.ak.auth.xpc lookup failed, akd/adid were enabled but unloaded, and appleaccountd was off. Enabling these brokers and restarting userspace restored the password/two-factor stage. CommCenter received carrier SMS while imagent was off; enabling/loading imagent and respringing restored user-confirmed verification SMS delivery.

Absd exports com.apple.absinthed. With absd off, AppleAccount NAC/Absinthe initialization failed with -44303/-4403 and iCloud login returned HTTP 400. Enabling/loading only absd plus respring changed NAC initialization to success. This is a verified authentication-stage role; the old Contacts attribution remains unsupported.

Later verification still failed. Restoring cdpd, followupd and identityservicesd together, then restarting userspace, was followed by user-confirmed full Apple Account sign-in. Logs show LoginAndUpdateAccount Authenticated=1/error=0 and successful Messages/FaceTime service sign-ins. The final three were tested as a set; individual responsibility for the preceding reset/timeout error was not isolated. FaceTime sign-in is not a tested FaceTime call.

Current Critical classification includes absd, imagent, identityservicesd, cdpd and followupd alongside earlier critical dependencies. Account On includes the complete repaired set and SMS prerequisite; account/Messages group Off preserves those Critical jobs. Individual switches remain editable with warnings. New builtin targets keep absd/cdpd/followupd On. Config: 512 targets, 322 Off / 190 On; all Critical enabled entries use capital NO. This supersedes older KEEP/recommendation and 325/187 counts in historical sections. Explicit custom Off choices remain explicit; installing an update does not automatically apply presets.

Further tests must be recorded by feature and state, with respring/userspace restart after changes. Full iCloud sync, App Store downloads, actual FaceTime/phone calls and every service combination are not established by sign-in or the memory allocation stress test. No net RAM or battery-life gain is claimed. Larger suspended apps in a pre-repair sample were Sudoku (~295 MB), Sileo (~108 MB) and WhatsApp (~101 MB); mostly small remaining helpers did not establish another worthwhile safe daemon disable.

## Photos sharing/export repair — v1.0.14

Photos reported Unable to Share with explicit XPC lookup error 3 / NSCocoaErrorDomain 4099 for disabled com.apple.photos.ImageConversionService. Restoring only that job and respringing repaired the user-tested Camera photo/screenshot sharing path. With the image repair retained, Camera video sharing independently failed and logs named disabled com.apple.photos.VideoConversionService. Restoring only the video job and respringing repaired the same video test. Both are now Critical/default On, with uppercase NO in the matching configs. This identifies these export failures; it does not prove the cause of the earlier disappearing WhatsApp chat picker or all media-format combinations.

Overrides adds a Photos sharing and export subpage. Group Off preserves both Critical jobs, so its group Off button is unavailable; individual warned switches remain editable. On restores both and shared assetsd/pkd/sharingd. Current config: 512 targets, 320 Off / 192 On, superseding older counts.

## App Store free-download repair — v1.0.15

Get spun briefly and returned. Logs showed a successful HTTP response followed by Payment Sheet Failed. Restoring amsengagementd alone plus respring did not repair it; PassKit then explicitly reported missing com.apple.passd.in-app-payment. Restoring only passd plus respring restored the password/confirmation sheet, free-app download, installation and user-confirmed launch. The PassbookUISceneService override stayed off; the separate PassbookUIService launched for confirmation. Turning amsengagementd back off and respringing still allowed a second new free app to download and open. It remains optional; its previous telemetry-only description was incomplete.

Passd is Critical/default On and an App Store shared prerequisite. Wallet group Off preserves it while leaving the interface switchable; individual warned switches remain available. Paid purchases, Apple Pay and Wallet passes remain untested. Current matching config: 512 targets, 319 Off / 193 On. FaceTime calls were unavailable to test because the user had no test contact; avconferenced was restored Off. Cloud sync testing is pending.

Deployment v1.0.14 / build 15: source bb70111567f69f3fdbcd31cf29332267ecd11d67; Actions run 38223458480 passed Python/Swift checks and iOS packaging. Release IPA SHA256 2503408a28f6cb4bc1e2b084b1a625d4cd17bfe56c354e9f6f04146cbbd52d24 matched GitHub digest and embedded version. TrollStore installation returned 0, installed Info.plist matched, and process 12682 was present after opening following userspace restart. New sharing subpage user check remains pending.

### Protected iCloud key repair and menu controls — v1.0.15

Drive/Photos initially stalled after their optional jobs were enabled. CloudKit explicitly failed user-key sync at missing ProtectedCloudKeySyncing. Enabling that job and restarting userspace made it reachable, but a two-hour retry deadline deferred recovery. Removing only nextRegistrySync via mobile CFPreferences and restarting that job exposed the next missing endpoint, com.apple.SecureBackupDaemon, at FetchMetaData. After restoring it and requesting another retry, logs showed SynchronizeKeys complete: success. The user completed the account notice and confirmed it cleared, then restarted userspace. Existing Drive files appeared; a photo saved to Drive was confirmed on the web and downloaded intact. The first Save to Files attempt unexpectedly returned Home, while the second passed; that transient failure has not been isolated. Photos upload remained stalled. Contextstored is temporarily restored for a separate comparison of Photos CoreDuet context failures; it is not yet established as the cause of the upload stall.

Both key-recovery jobs are now Critical shared account/Drive/Photos prerequisites. SecureBackupDaemon now defaults On; ProtectedCloudKeySyncing already defaulted On but had been manually disabled. Current matching config: 512 targets, 318 Off / 194 On. The retry-deadline diagnostic is not part of automatic group actions. No account/key reset was performed by the diagnostic code. Preserve the phone’s existing passcode/jailbreak setup; the user identifies this as palera1n on A11.

Overrides menu rows now provide On/Off beside the link, retaining the subpage controls and Critical preservation policy. Deployment v1.0.15 / build 16: source 5647956a28d966b527f0e9de544af6887890f67d; Actions 38227081398 passed Python/Swift checks and packaging; IPA SHA256 2e920d6f1b0a81036f9584673788c1e21fc362859f01c55063872ac1db3832f5 matched release digest/version. TrollStore installation returned 0 and installed version matched. Menu-button user verification remains pending.

### Photos scheduling comparison — v1.0.16

Contextstored exports com.apple.coreduetd.context. After restoring it and restarting userspace, the scheduled Photos retry generated/uploaded derivatives and the user confirmed two fresh photos synced and appeared on the web. Turning only contextstored back off and restarting reproduced a third-photo stall. At the retry, Photos/DAS context reads/writes failed and DAS rejected com.apple.cloudphotod.sync.discretionary for DataBudgetPolicy (remaining ~546 KB vs predicted 10 MiB) and NetworkQualityPolicy (required 100, observed Wi-Fi/cellular 0). The restart ThunderingHerdPolicy was no longer a listed blocker. It was restored for a final same-photo recovery comparison.

Contextstored is Critical/default On and included with dasd in the Photos group. Current matching config: 512 targets, 317 Off / 195 On. The user selected returning iCloud Drive/Photos sync services to their original Off states after testing; Critical account/key/context dependencies stay enabled. Other Siri learning/prediction jobs were not restored for this comparison. Final recovery/menu-button checks remain pending. No net RAM saving or exhaustive combination test is claimed.

### Final verification / normal profile — v1.0.16

Restoring contextstored again completed the same third-photo upload after the scheduled retry. Save to Files into Drive passed again and was visible on the web. The user enabled Drive/Photos from the new menu On buttons, used Tools userspace restart, and reported fresh-restart cloud checks passed. Both menu Off buttons followed by userspace restart then left bird/cloudd/cloudphotod/assetsd.nebulad disabled/unloaded; repaired Critical account/key/context/scheduling dependencies stayed enabled/loaded. The initial unexpected return Home did not recur in later tests, but its precise cause was not isolated.

Final AirDrop policy read returned 0; profiled was restored disabled/unloaded afterward. Diagnosticd/ReportCrash were restored disabled/unloaded. Recording, widgets/Live Activities, wallpaper/captions/Wallet interface hosts, amsengagementd and avconferenced stayed Off. Baseline comparison in both saved domains found only 12 retained enable changes: appleaccountd, absd, cdpd, followupd, identityservicesd, imagent, image/video conversion, passd, ProtectedCloudKeySyncing, SecureBackupDaemon, contextstored. Akd/adid were already saved On and were reloaded earlier.

Settled spindump: SpringBoard 0.015 CPU seconds / 15.00 seconds = 0.10% of one core; thermal pressure 0. Contextstored footprint 6,880 KB, CPU 0.008 seconds. This is a short settled sample, not net RAM/battery proof. No additional worthwhile safe daemon disable was established. FaceTime calls, paid purchases/Apple Pay, exhaustive combinations, memory background-cleanup behavior and the WhatsApp Meta AI action remain unverified/unresolved.

Deployment v1.0.16/build 17: source 0f730f88a1e417af877dc53e23084103b2656b74; Actions 38229538314 passed 20 Python and Swift checks plus iOS build/package. IPA SHA256 67024e577f83ce2e62c08d24d405619edd40f1234b771ee44d2c50f7aeb1a284 matched release digest/version. TrollStore returned 0, installed version matched, launch was verified and menu On/Off actions were exercised. Final config: 512 targets, 317 Off / 195 On, matching app/notes. Bare-repository staging was left to the user.


### Lock Screen playback hosts — 2026-10-12, v1.0.17

The tested Lock Screen/Notification Center media group needs PosterBoard + liveactivitiesd, with PaperBoard still Off. Keep all three optional/default Off as explicitly requested; complete group Off must remain available. PosterBoard is shared with Wallpaper, while widget updates now controls chronod separately. Restart userspace after media-group changes. Config now matches 512 targets, 318 Off / 194 On; existing Critical NO entries are unchanged. See the [daemon test notes](scripts/daemons/README.md) for the On/Off comparison and recorded MediaRemoteUI rendering failure.


### SpringBoard interface controls — v1.0.20

Control Center and App Library have no separate launchd jobs in this phone's inspected configuration. The app deploys a minimal SpringBoard-only rootless companion to block their presentation/capability methods, with individual and menu On/Off switches, saved-versus-current-PID reporting and removal. Respring applies changes. This does not establish a reduction in allocated RAM. All 512 daemon targets remain unchanged (318 Off / 194 On).

The prototype passed Off/On checks, but packaged v1.0.18 wrote settings to the bootstrap mobile home (/var/jb/var/mobile) instead of SpringBoard's native /var/mobile. It therefore saved Off without applying it. The corrected script uses the native path and account UID/GID for ownership; after a respring the runtime report showed all requested hooks active, and the user confirmed the interfaces were gone. A regression test covers distinct bootstrap/native homes.

The user requested removing dock/status-bar controls because they only hide views; those switches and layout hooks were removed and both restored. The remaining switches block access to Control Center/App Library; RAM savings remain unverified. Attempts to remove Today View did not work and their hooks were removed, with the user's agreement. Keep pkd and repaired Critical services enabled. No jetsam-limit change or persistent memory stress allocation was introduced.


The installed v1.0.20/build 21 app passed All On → respring → both interfaces available, followed by Off → respring → both inaccessible, confirmed by the user. Dock/status bar were restored and their switches removed. Final saved settings and current-PID runtime report both have Control Center/App Library disabled. CI run 38244546744 passed 23 Python tests, Swift memory/stress/group checks and iOS build/package. Source ca663064295eb14b72a669ececcfcf37eeda7277; IPA SHA256 35e71d9caee42bc50b7e251d69633b10420ebf44ca925c2fbb101d33c6d4bdca matched the release digest and embedded source/version; TrollStore returned 0 and the app process was observed running. Both saved launchd domains matched the pre-interface profile exactly. A settled 10-second sample measured SpringBoard at 0.010 CPU seconds (0.10% of one core), footprint 64.13 MB. That is not a comparative RAM/battery benchmark or exhaustive stress proof.

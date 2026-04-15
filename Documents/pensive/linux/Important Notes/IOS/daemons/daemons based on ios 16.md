Most conservative revision I can stand behind. Undocumented/private daemons are intentionally described generically rather than guessed at.

Conservative legend:

- `yes` = generally safe to disable for most personal devices
- `if-unused` = usually safe only if you do not use that feature
- `no` = maybe disableable in some setups, but I would not broadly recommend it
- `NO` = core/critical; do not disable

| #   | daemon                        | description                                                                                  | safe_to_disable_for_most_users |
| --- | ----------------------------- | -------------------------------------------------------------------------------------------- | ------------------------------ |
| 1   | ABDatabaseDoctor              | Contacts database repair helper                                                              | yes                            |
| 2   | abm-helper                    | Apple Business Manager                                                                       | yes                            |
| 3   | absd                          | Address Book Source Daemon for Contacts accounts and sync                                    | no                             |
| 4   | accessibility.axassetsd       | Accessibility asset downloads, such as voices                                                | if-unused                      |
| 5   | accessibility.axremoted       | Remote accessibility services                                                                | if-unused                      |
| 6   | accessibility.heard           | Disables Background white noise and hearing aids                                             | NO                             |
| 7   | accessibility.motiontrackingd | Accessibility motion/head-tracking support                                                   | if-unused                      |
| 8   | accessoryupdaterd             | Firmware updates for Apple/supported accessories                                             | if-unused                      |
| 9   | accountsd                     | System accounts database and access management                                               | NO                             |
| 10  | activityawardsd               | Fitness/Activity awards processing                                                           | if-unused                      |
| 11  | adid                          | apple id account login                                                                       | NO                             |
| 12  | AdminLite                     | system watchdog daemon                                                                       | NO                             |
| 13  | afcd                          | Apple File Conduit for USB file access and Finder/iTunes file sharing                        | yes                            |
| 14  | aggregated                    | System analytics aggregation                                                                 | yes                            |
| 15  | aggregated.addaily            | Advertising-related daily aggregation task                                                   | yes                            |
| 16  | akd                           | apple id account login                                                                       | NO                             |
| 17  | amsaccountsd                  | Apple Media Services account handling                                                        | no                             |
| 18  | amsengagementd                | Apple Media Services engagement telemetry                                                    | yes                            |
| 19  | analyticsd                    | System analytics and diagnostics collection                                                  | yes                            |
| 20  | and                           | Private Apple daemon, unclear public role                                                    | no                             |
| 21  | announced                     | Announce Notifications / Announce Messages support                                           | if-unused                      |
| 22  | anomalydetectiond             | celerometer and gyroscope data for Crash Detection and Fall Detection                        | yes                            |
| 23  | ap.adprivacyd                 | Advertising privacy policy enforcement                                                       | yes                            |
| 24  | ap.promotedcontentd           | Promoted content and App Store recommendations/ads                                           | yes                            |
| 25  | appleaccountd                 | Apple Account management and sync                                                            | NO                             |
| 26  | applecamerad                  | Core camera hardware interface                                                               | no                             |
| 27  | AppleCredentialManager..      | Apple credential management service                                                          | no                             |
| 28  | AppSSODaemon                  | Single Sign-On service, mainly enterprise                                                    | if-unused                      |
| 29  | appstorecomponentsd           | App Store support components                                                                 | no                             |
| 30  | appstored                     | Core App Store download/update service                                                       | no                             |
| 31  | apsd                          | Apple Push Notification service daemon                                                       | NO                             |
| 32  | arkitd                        | ARKit service for augmented-reality apps                                                     | if-unused                      |
| 33  | asd                           | Private Apple daemon; public role not clearly documented                                     | no                             |
| 34  | askpermissiond                | Family Sharing Ask to Buy approvals                                                          | if-unused                      |
| 35  | AssetCacheLocatorService      | Finds local Apple caching servers on the network                                             | yes                            |
| 36  | assetsd                       | Photos asset library , disabling disables photos and screenshots vieweing in photos          | NO                             |
| 37  | assetsd.nebulad               | Photos/iCloud Photos asset sync helper                                                       | if-unused                      |
| 38  | assistant_cdmd                | Siri assistant data management                                                               | if-unused                      |
| 39  | atc                           | Legacy Finder/iTunes sync helper                                                             | yes                            |
| 40  | AuthenticationServicesCore    | AuthenticationServices XPC service for sign-in/passkey flows                                 | no                             |
| 41  | avatarsd                      | Memoji/avatar rendering and sync                                                             | if-unused                      |
| 42  | avconferenced                 | Audio/video conferencing service                                                             | if-unused                      |
| 43  | backboardd                    | Core iOS input, display, and event-routing service                                           | NO                             |
| 44  | backgroundassets.user         | Background asset downloads for apps/system content                                           | no                             |
| 45  | BarcodeSupport                | Barcode support service                                                                      | if-unused                      |
| 46  | batteryintelligenced          | Battery intelligence and optimized charging support                                          | yes                            |
| 47  | betaenrollmentd               | Beta enrollment management                                                                   | yes                            |
| 48  | biomed                        | Biome knowledge-store service                                                                | no                             |
| 49  | biomesyncd                    | Biome sync service                                                                           | no                             |
| 50  | biometrickitd.mesa            | Biometric hardware interface for Touch ID / Face ID                                          | no                             |
| 51  | bird                          | iCloud Drive / CloudDocs sync daemon                                                         | if-unused                      |
| 52  | BlueTool                      | Bluetooth controller bring-up and management                                                 | no                             |
| 53  | bluetoothd                    | Core Bluetooth stack daemon                                                                  | no                             |
| 54  | bluetoothuiservice            | Bluetooth pairing/UI support                                                                 | if-unused                      |
| 55  | bluetoothuserd                | User-level Bluetooth support                                                                 | if-unused                      |
| 56  | bookassetd                    | Apple Books asset management                                                                 | if-unused                      |
| 57  | bookdatastored                | Apple Books data storage and sync                                                            | if-unused                      |
| 58  | bootps                        | BOOTP/DHCP service used for tethering or USB networking                                      | no                             |
| 59  | BTServer.avrcp                | Bluetooth media control profile support, Prevents pause/play on the headphones if disabled   | NO                             |
| 60  | BTServer.le                   | Bluetooth Low Energy support, saves power                                                    | NO                             |
| 61  | BTServer.map                  | Bluetooth Message Access Profile support                                                     | if-unused                      |
| 62  | BTServer.pbap                 | Bluetooth Phone Book Access Profile support                                                  | if-unused                      |
| 63  | businessservicesd             | Apple business-related services; public role unclear                                         | no                             |
| 64  | cache_delete                  | Storage cleanup and cache purge service                                                      | no                             |
| 65  | calaccessd                    | Calendar database access control                                                             | no                             |
| 66  | CallHistorySyncHelper         | iCloud sync helper for call history                                                          | if-unused                      |
| 67  | captiveagent                  | Captive portal detection for public Wi‑Fi                                                    | if-unused                      |
| 68  | carkitd                       | CarPlay core daemon                                                                          | if-unused                      |
| 69  | CarPlayApp                    | CarPlay UI process                                                                           | if-unused                      |
| 70  | cdpd                          | Private data-protection/escrow security service                                              | no                             |
| 71  | certui.relay                  | Certificate trust prompt relay                                                               | no                             |
| 72  | cfnetwork.AuthBrokerAgent     | Network authentication broker                                                                | NO                             |
| 73  | cfnetwork.cfnetworkagent      | Core networking agent                                                                        | NO                             |
| 74  | cfprefsd.xpc.daemon           | CoreFoundation preferences daemon                                                            | NO                             |
| 75  | cfprefsd.xpc.daemon.system    | System-level preferences daemon                                                              | NO                             |
| 76  | checkerboardd                 | Recovery/restore or diagnostic UI service                                                    | no                             |
| 77  | chronod                       | Widget timelines and scheduled widget updates                                                | if-unused                      |
| 78  | ckdiscretionaryd              | CloudKit background transfer service                                                         | if-unused                      |
| 79  | ClarityBoard                  | Assistive Access / simplified accessibility UI                                               | if-unused                      |
| 80  | ClipServices.clipserviced     | App Clips management service                                                                 | if-unused                      |
| 81  | cloudd                        | CloudKit syncing daemon                                                                      | if-unused                      |
| 82  | cloudpaird                    | iCloud-based pairing info sync                                                               | if-unused                      |
| 83  | cloudphotod                   | iCloud Photos background sync                                                                | if-unused                      |
| 84  | CloudSettingsSyncAgent        | iCloud settings sync agent                                                                   | if-unused                      |
| 85  | cmfsyncagent                  | Private Apple sync agent, unclear public role                                                | no                             |
| 86  | commandandcontrol             | Voice Control accessibility daemon                                                           | if-unused                      |
| 87  | CommCenter                    | Core cellular and telephony daemon                                                           | NO                             |
| 88  | CommCenter-noncellular        | Network-service variant for non-cellular devices                                             | no                             |
| 89  | CommCenterMobileHelper        | Cellular helper for carrier/config tasks                                                     | NO                             |
| 90  | CommCenterRootHelper          | Privileged cellular configuration helper                                                     | NO                             |
| 91  | companion_proxy               | Apple Watch companion proxy service                                                          | if-unused                      |
| 92  | companionauthd                | Apple Watch authentication and pairing support                                               | if-unused                      |
| 93  | configd                       | System Configuration and network configuration                                               | NO                             |
| 94  | contacts.donation-agent       | Contacts suggestions donation agent                                                          | yes                            |
| 95  | contactsd                     | Contacts database daemon                                                                     | no                             |
| 96  | containermanagerd             | App sandbox and container manager                                                            | NO                             |
| 97  | containermanagerd.system      | System-level container manager                                                               | NO                             |
| 98  | contextstored                 | Stores context for Siri/proactive features                                                   | yes                            |
| 99  | coordinated                   | File/document coordination service                                                           | no                             |
| 100 | CoreAuthentication.daemon     | Local authentication service for biometric/passcode prompts                                  | NO                             |
| 101 | corecaptured                  | Low-level diagnostic capture service                                                         | yes                            |
| 102 | coredatad                     | Core Data support daemon used by system apps/frameworks                                      | no                             |
| 103 | coreduetd                     | On-device usage learning and prediction service                                              | no                             |
| 104 | coreidvd                      | Digital ID / Wallet identity verification support                                            | if-unused                      |
| 105 | corerepaird                   | Repair status / parts validation service                                                     | if-unused                      |
| 106 | coreservices.useractivityd    | NSUserActivity and Handoff support                                                           | if-unused                      |
| 107 | corespeechd                   | Core speech service for Siri / voice trigger support                                         | if-unused                      |
| 108 | corespotlightservice          | Core Spotlight indexing/search support                                                       | yes                            |
| 109 | countryd                      | Country/region determination service                                                         | no                             |
| 110 | ctkd                          | CryptoTokenKit daemon for smart cards/token credentials                                      | if-unused                      |
| 111 | dasd                          | Duet Activity Scheduler for background tasks                                                 | NO                             |
| 112 | dataaccess.dataaccessd        | Exchange, CalDAV, and CardDAV sync service                                                   | if-unused                      |
| 113 | DataDetectorsSourceAccess     | Data Detectors source-access service                                                         | no                             |
| 114 | datastored                    | Private Apple data-storage service, unclear public role                                      | no                             |
| 115 | deferredmediad                | Deferred camera/photo processing service                                                     | no                             |
| 116 | deleted_helper                | Helper for cache deletion and storage cleanup                                                | no                             |
| 117 | DesktopServicesHelper         | CoreServices file/metadata helper                                                            | no                             |
| 118 | device-o-matic                | Private device/hardware helper service                                                       | no                             |
| 119 | deviceaccessd                 | Device-access / permission-mediation service                                                 | no                             |
| 120 | devicecheckd                  | DeviceCheck / App Attest support service                                                     | no                             |
| 121 | devicedatasetresd             | Private Apple daemon, unclear public role                                                    | no                             |
| 122 | devicemanagementclient..      | Managed device-management client component                                                   | if-unused                      |
| 123 | devicemanagementclient..      | Managed enrollment / device-management helper                                                | if-unused                      |
| 124 | dhcp6d                        | DHCPv6 client daemon                                                                         | NO                             |
| 125 | diagnosticd                   | System diagnostics processing                                                                | yes                            |
| 126 | diagnosticextensionsd         | Diagnostic extension collection                                                              | yes                            |
| 127 | dietapplecamerad              | Lightweight camera service used by specific pipelines                                        | no                             |
| 128 | dietappleh13camerad           | Hardware-specific lightweight camera daemon                                                  | no                             |
| 129 | diskarbitrationd              | Disk arbitration and mount management                                                        | no                             |
| 130 | diskimagesiod                 | Disk image mount support service                                                             | NO                             |
| 131 | diskimagesiod.ram             | RAM disk image support service                                                               | yes                            |
| 132 | distnoted.xpc.daemon          | Distributed notifications service                                                            | NO                             |
| 133 | dmd                           | Declarative Device Management daemon                                                         | if-unused                      |
| 134 | DMHelper                      | Device-management helper                                                                     | if-unused                      |
| 135 | donotdisturbd                 | Do Not Disturb / Focus management                                                            | NO                             |
| 136 | dprivacyd                     | Differential Privacy telemetry service                                                       | yes                            |
| 137 | DragUI.druid                  | Drag-and-drop UI support                                                                     | NO                             |
| 138 | driverkitd                    | DriverKit user-space driver host service                                                     | no                             |
| 139 | dt.automationmode-writer      | Developer Automation Mode support task                                                       | yes                            |
| 140 | dt.AutomationModeUI           | Developer Automation Mode UI service                                                         | yes                            |
| 141 | dt.fetchsymbolsd              | Developer symbol fetch service                                                               | yes                            |
| 142 | dt.previewsd                  | Developer preview support service                                                            | yes                            |
| 143 | dt.remotepairingdeviced       | Developer wireless pairing daemon                                                            | yes                            |
| 144 | duetexpertd                   | Duet/Siri suggestions expert service                                                         | if-unused                      |
| 145 | email.maild                   | Apple Mail background sync/fetch                                                             | if-unused                      |
| 146 | EscrowSecurityAlert           | Security escrow alert service                                                                | yes                            |
| 147 | exchangesyncd                 | Microsoft Exchange sync daemon                                                               | if-unused                      |
| 148 | facemetricsd                  | Face ID metrics / attention support                                                          | no                             |
| 149 | factory.NFQRCoded             | Factory/diagnostic QR or NFC code support                                                    | yes                            |
| 150 | fairplayd.H2                  | FairPlay DRM daemon, apps installed from the app store dont launch if disabled               | NO                             |
| 151 | fairplaydeviceidentityd       | FairPlay device identity support, apps installed from the app store dont launch if disabled  | NO                             |
| 152 | familycircled                 | Family Sharing management daemon                                                             | if-unused                      |
| 153 | FamilyControlsAgent           | Family Controls / Screen Time family enforcement                                             | if-unused                      |
| 154 | familynotificationd           | Family Sharing notifications daemon                                                          | if-unused                      |
| 155 | fdrhelper                     | Factory restore / calibration key helper                                                     | no                             |
| 156 | FileCoordination              | File coordination service                                                                    | no                             |
| 157 | FileProvider                  | Files app and third-party file-provider support                                              | no                             |
| 158 | filesystems.apfs_iosd         | APFS filesystem management daemon                                                            | NO                             |
| 159 | filesystems.livefileproviderd | Live File Provider filesystem support                                                        | no                             |
| 160 | filesystems.smbclientd        | SMB client daemon for network shares                                                         | if-unused                      |
| 161 | filesystems.userfs_helper     | User-space filesystem helper                                                                 | no                             |
| 162 | filesystems.userfsd           | User-space filesystem daemon                                                                 | no                             |
| 163 | financed                      | Apple financial services support                                                             | if-unused                      |
| 164 | fitcore                       | Fitness framework core service                                                               | if-unused                      |
| 165 | fitcore.session               | Fitness active-session support service                                                       | if-unused                      |
| 166 | fitnesscoachingd              | Fitness coaching notifications                                                               | if-unused                      |
| 167 | followupd                     | Settings follow-up suggestions/account reminders                                             | yes                            |
| 168 | fontservicesd                 | System font management and font downloads                                                    | no                             |
| 169 | fs_task_scheduler             | Filesystem maintenance scheduler                                                             | no                             |
| 170 | fseventsd                     | Filesystem events daemon                                                                     | NO                             |
| 171 | ftp-proxy-embedded            | Legacy FTP proxy service                                                                     | yes                            |
| 172 | fullkeyboardaccess            | Accessibility Full Keyboard Access support                                                   | if-unused                      |
| 173 | fusiond                       | Private Apple daemon, unclear public role                                                    | no                             |
| 174 | GameController.game..         | Game controller framework daemon                                                             | if-unused                      |
| 175 | geod                          | GeoServices daemon for Maps/geocoding                                                        | no                             |
| 176 | gpsd                          | GPS hardware daemon                                                                          | no                             |
| 177 | griddatad                     | Private Apple grid/layout data service                                                       | no                             |
| 178 | GSSCred                       | Kerberos credential daemon                                                                   | if-unused                      |
| 179 | handwritingd                  | Handwriting recognition service                                                              | if-unused                      |
| 180 | hangreporter                  | Reports app/system hangs                                                                     | yes                            |
| 181 | hangtracerd                   | Collects traces for app/system hangs                                                         | yes                            |
| 182 | healthappd                    | Health app support daemon                                                                    | if-unused                      |
| 183 | healthrecordsd                | Health Records sync/storage service                                                          | if-unused                      |
| 184 | icloud.findmydeviced          | Find My device support daemon                                                                | if-unused                      |
| 185 | icloud.fmfd                   | Find My People support daemon                                                                | if-unused                      |
| 186 | icloud.fmflocatord            | Find My location reporting daemon                                                            | if-unused                      |
| 187 | icloud.searchpartyd           | Find My network / AirTag crowd-location daemon                                               | if-unused                      |
| 188 | icloudsubscriptionopti..      | iCloud subscription/storage upsell service                                                   | yes                            |
| 189 | iconservices.iconservi..      | App icon rendering and cache service                                                         | no                             |
| 190 | idamd                         | Private Apple identity/auth daemon                                                           | no                             |
| 191 | idcredd                       | Identity credential service for digital IDs                                                  | if-unused                      |
| 192 | identityservicesd             | Identity Services daemon for iMessage/FaceTime                                               | no                             |
| 193 | idscredentialsagent           | Identity Services credential agent                                                           | no                             |
| 194 | idsremoteurlconnection..      | Identity Services network agent                                                              | no                             |
| 195 | imagent                       | iMessage account and messaging agent                                                         | no                             |
| 196 | imautomatichistorydele..      | Automatic message-history deletion support                                                   | yes                            |
| 197 | imtransferagent               | Transfers iMessage attachments/media                                                         | no                             |
| 198 | inboxupdaterd                 | Inbox update helper; public role not well documented                                         | no                             |
| 199 | ind                           | Private Apple daemon, unclear public role                                                    | no                             |
| 200 | installcoordination_proxy     | Proxy helper for install coordination                                                        | no                             |
| 201 | installcoordinationd          | Coordinates app installs and updates                                                         | no                             |
| 202 | intelligenceplatformd         | On-device intelligence platform for Siri/predictions                                         | if-unused                      |
| 203 | IOAccelMemoryInfoCollector    | GPU/graphics memory diagnostics collector                                                    | yes                            |
| 204 | iomfb_bics_daemon             | Display subsystem calibration/framebuffer helper                                             | no                             |
| 205 | iomfb_fdr_loader              | Factory display calibration loader                                                           | no                             |
| 206 | iosdiagnosticsd               | Apple diagnostics/support daemon                                                             | yes                            |
| 207 | ioupsd                        | Private power-related daemon, unclear public role                                            | no                             |
| 208 | itunesstored                  | Store services daemon for media purchases/downloads                                          | if-unused                      |
| 209 | jetsamproperties.D21          | Memory-pressure and Jetsam configuration                                                     | NO                             |
| 210 | jetsamproperties.D211         | Hardware-specific Jetsam configuration                                                       | NO                             |
| 211 | keychainsharingmessagingd     | Keychain sharing / iCloud Keychain messaging support                                         | if-unused                      |
| 212 | knowledgeconstructiond        | Builds knowledge data for Siri/search                                                        | yes                            |
| 213 | languageassetd                | Downloads language assets such as dictionaries/voices                                        | if-unused                      |
| 214 | linkd                         | Linking service for intents/deep links/app routing                                           | no                             |
| 215 | liveactivitiesd               | Live Activities support                                                                      | if-unused                      |
| 216 | localizationswitcherd         | Localization/language-switch helper                                                          | if-unused                      |
| 217 | locationd                     | Core Location daemon                                                                         | no                             |
| 218 | locationpushd                 | Location-triggered push-notification support                                                 | if-unused                      |
| 219 | logd                          | Unified logging daemon                                                                       | no                             |
| 220 | logd_helper                   | Helper for unified logging                                                                   | no                             |
| 221 | lsd                           | LaunchServices daemon for app registration/launching                                         | NO                             |
| 222 | lsd.system                    | System-level LaunchServices daemon                                                           | NO                             |
| 223 | lskdd                         | Private Apple daemon, unclear public role                                                    | NO                             |
| 224 | managedconfiguration..        | Managed passcode compliance prompt service                                                   | if-unused                      |
| 225 | managedconfiguration..        | Configuration profile and managed-policy daemon                                              | if-unused                      |
| 226 | ManagedSettingsAgent          | Managed Settings / Screen Time enforcement                                                   | if-unused                      |
| 227 | maps.destinationd             | Maps destination suggestions service                                                         | if-unused                      |
| 228 | maps.geocorrectiond           | Maps location-correction / feedback daemon                                                   | if-unused                      |
| 229 | maps.pushdaemon               | Maps notification daemon                                                                     | if-unused                      |
| 230 | matd                          | Private measurement/telemetry daemon                                                         | no                             |
| 231 | mDNSResponder                 | Bonjour and Multicast DNS daemon                                                             | NO                             |
| 232 | mDNSResponderHelper           | Helper for Bonjour/mDNS networking                                                           | NO                             |
| 233 | mdt                           | Private Apple daemon, unclear public role                                                    | no                             |
| 234 | mediaanalysisd                | On-device media analysis for Photos                                                          | yes                            |
| 235 | mediaanalysisd.service        | XPC service for media analysis tasks                                                         | yes                            |
| 236 | mediaartworkd                 | Downloads/manages album art and related artwork                                              | if-unused                      |
| 237 | medialibraryd                 | Media library database daemon                                                                | if-unused                      |
| 238 | mediaparserd                  | Parses media files and containers                                                            | no                             |
| 239 | mediaremoted                  | Remote media-control daemon                                                                  | no                             |
| 240 | mediaserverd                  | Core audio/video playback service                                                            | no                             |
| 241 | mediasetupd                   | Media device setup support, such as HomePod/Apple TV                                         | if-unused                      |
| 242 | mediastream.mstreamd          | Shared Photo Streams / related media stream service                                          | if-unused                      |
| 243 | memory-maintenance            | Memory-pressure housekeeping task                                                            | no                             |
| 244 | merchantd                     | Apple Pay merchant-session / transaction support                                             | if-unused                      |
| 245 | metrickitd                    | MetricKit performance metrics collection                                                     | yes                            |
| 246 | misagent                      | Provisioning profile and signing support agent, disabling will cause self signed to not open | NO                             |
| 247 | mlmodelingd                   | Downloads/manages machine-learning models                                                    | yes                            |
| 248 | mlruntimed                    | Core ML runtime daemon                                                                       | no                             |
| 249 | mobile.assertion_agent        | Manages process assertions and background-execution claims                                   | NO                             |
| 250 | mobile.cache_delete..         | Clears app container caches                                                                  | no                             |
| 251 | mobile.cache_delete_daily     | Daily cache-cleanup task                                                                     | no                             |
| 252 | mobile.cache_delete..backup   | Cache cleanup related to device backups                                                      | yes                            |
| 253 | mobile.heartbeat              | System heartbeat/watchdog-related service                                                    | no                             |
| 254 | mobile.house_arrest           | USB file-sharing service for app containers                                                  | yes                            |
| 255 | mobile.insecure_notificatio.. | USB communication with a computer                                                            | if-unused                      |
| 256 | mobile.installd               | Core app installation daemon                                                                 | no                             |
| 257 | mobile.keybagd                | Keybag and data-protection key management                                                    | NO                             |
| 258 | mobile.lockdown               | Lockdown pairing, trust, and host communication                                              | if-unused                      |
| 259 | mobile.MCInstall              | Mobile configuration install helper                                                          | if-unused                      |
| 260 | mobile.notification_proxy     | Lockdown notification proxy for host communication                                           | if-unused                      |
| 261 | mobile.storage_mounter        | Mount service for developer/external storage images                                          | if-unused                      |
| 262 | mobile.storage_mounter..      | Proxy for storage mount operations                                                           | if-unused                      |
| 263 | mobile.usermanagerd           | DEVICE WILL BOOT LOOP IF TURNED OFF                                                          | NO                             |
| 264 | mobile_installation_proxy     | Proxy service for app installation operations                                                | no                             |
| 265 | mobileactivationd             | Activation status and server-communication daemon                                            | NO                             |
| 266 | mobileassetd                  | Downloads Apple mobile assets such as trust/language files                                   | no                             |
| 267 | mobilebackup2                 | Finder/iTunes local backup service                                                           | if-unused                      |
| 268 | mobilecheckpoint.check..      | Update/checkpoint validation daemon                                                          | no                             |
| 269 | MobileFileIntegrity           | AMFI code-signing enforcement                                                                | NO                             |
| 270 | mobilegestalt.xpc             | System capability and hardware-property query service                                        | NO                             |
| 271 | mobilerepaird                 | Parts pairing and repair-status service                                                      | if-unused                      |
| 272 | mobilestoredemod              | Retail demo mode daemon                                                                      | yes                            |
| 273 | mobilestoredemodhelper        | Retail demo mode helper                                                                      | yes                            |
| 274 | mobiletimerd                  | Clock alarms and timers support                                                              | no                             |
| 275 | momentsd                      | Photos Memories/moments curation                                                             | if-unused                      |
| 276 | MTLAssetUpgraderD             | Metal asset/shader upgrade service                                                           | no                             |
| 277 | mtmergeprops                  | Multi-touch calibration/property merge support                                               | no                             |
| 278 | nand.aspcarry                 | Private NAND/flash-storage support task                                                      | no                             |
| 279 | nand_task_scheduler           | Flash-storage maintenance scheduler                                                          | no                             |
| 280 | nanobackupd                   | Apple Watch backup daemon                                                                    | if-unused                      |
| 281 | nanoprefsyncd                 | Apple Watch preference-sync daemon                                                           | if-unused                      |
| 282 | nanoregistryd                 | CAUSES BOOTLOOP IF DISABLED                                                                  | NO                             |
| 283 | nanoregistrylaunchd           | Apple Watch registry launch helper                                                           | if-unused                      |
| 284 | nanotimekitcompaniond         | Apple Watch face/TimeKit companion sync                                                      | if-unused                      |
| 285 | naturallanguaged              | Natural Language framework daemon                                                            | if-unused                      |
| 286 | navd                          | Turn-by-turn navigation service for Maps                                                     | if-unused                      |
| 287 | ndoagent                      | Private Apple daemon, unclear public role                                                    | no                             |
| 288 | neagent-ios                   | Network Extension agent for VPNs/filters                                                     | if-unused                      |
| 289 | nearbyd                       | Nearby interaction and proximity service                                                     | if-unused                      |
| 290 | nehelper-embedded             | Network Extension helper service                                                             | if-unused                      |
| 291 | nesessionmanager              | DISABLING CAUSES MASSIVE POWER DRAW                                                          | NO                             |
| 292 | NetworkLinkConditioner        | Developer network-throttling tool                                                            | yes                            |
| 293 | networkserviceproxy           | Network proxy service used by Private Relay and related features                             | if-unused                      |
| 294 | newsd                         | Apple News background service                                                                | if-unused                      |
| 295 | nexusd                        | Private Apple network-related daemon                                                         | no                             |
| 296 | nfcd                          | NFC hardware daemon                                                                          | if-unused                      |
| 297 | nfrestore                     | NFC firmware/restore support service                                                         | if-unused                      |
| 298 | notifyd                       | Darwin notification center daemon                                                            | NO                             |
| 299 | nsurlsessiond                 | Background transfer daemon for URLSession networking                                         | no                             |
| 300 | online-auth-agent.xpc         | Online authentication helper for sign-in/activation flows                                    | no                             |
| 301 | osanalytics.osanalyticshelper | OS analytics formatting helper                                                               | yes                            |
| 302 | ospredictiond                 | System prediction daemon                                                                     | yes                            |
| 303 | parsec-fbf                    | Feedback/analytics service for Siri Search/Spotlight                                         | yes                            |
| 304 | parsecd                       | Siri Search / Spotlight web-query service                                                    | if-unused                      |
| 305 | pasteboard.pasted             | System pasteboard/clipboard daemon                                                           | no                             |
| 306 | pcapd                         | Packet-capture daemon for diagnostics                                                        | yes                            |
| 307 | peakpowermanagerd             | Performance and battery-aging management daemon                                              | no                             |
| 308 | peopled                       | People-intelligence daemon for contact suggestions                                           | yes                            |
| 309 | perfdiagsselfenabled          | Performance diagnostics task                                                                 | yes                            |
| 310 | PerfPowerServicesExtended     | Extended power/performance telemetry                                                         | yes                            |
| 311 | pfd                           | Packet Filter / firewall support daemon                                                      | no                             |
| 312 | photoanalysisd                | Photos analysis daemon for faces/objects/scenes                                              | yes                            |
| 313 | photos.ImageConversion..      | Image conversion service used by Photos/system frameworks                                    | no                             |
| 314 | photos.VideoConversion..      | Video conversion service used by Photos/system frameworks                                    | no                             |
| 315 | pipelined                     | Image/camera processing pipeline daemon                                                      | no                             |
| 316 | pluginkit.pkd                 | PlugInKit daemon for app extensions/widgets                                                  | no                             |
| 317 | pluginkit.pkreporter          | PlugInKit diagnostics reporter                                                               | yes                            |
| 318 | pointerUI.pointeruid          | Pointer UI daemon for mouse/trackpad support                                                 | if-unused                      |
| 319 | powerd                        | Core power-management daemon                                                                 | NO                             |
| 320 | powerdatad                    | Battery-usage data collection/reporting                                                      | yes                            |
| 321 | powerlogHelperd               | Power logging helper                                                                         | yes                            |
| 322 | PowerUIAgent                  | Power/battery-related UI alerts agent                                                        | no                             |
| 323 | preboardservice               | Pre-boot security / passcode UI service                                                      | NO                             |
| 324 | preboardservice_v2            | Updated pre-boot security UI service                                                         | NO                             |
| 325 | privacyaccountingd            | App Privacy Report accounting service                                                        | yes                            |
| 326 | proactiveeventtrackerd        | Tracks events for proactive Siri suggestions                                                 | yes                            |
| 327 | progressd                     | System progress-reporting daemon                                                             | no                             |
| 328 | protectedcloudstorage.p..     | Protected cloud key-syncing service                                                          | no                             |
| 329 | ProxiedCrashCopier.Prox..     | Copies crash logs from proxied/paired devices                                                | yes                            |
| 330 | proximitycontrold             | Proximity-based handoff/device-interaction service                                           | if-unused                      |
| 331 | ptpcamerad                    | PTP camera access daemon for importing photos                                                | if-unused                      |
| 332 | ptpd                          | PTP daemon used for photo transfer / related sync                                            | if-unused                      |
| 333 | purplebuddy.budd              | Setup Assistant daemon                                                                       | if-unused                      |
| 334 | PurpleReverseProxy            | Reverse proxy used by some USB/device communication flows                                    | if-unused                      |
| 335 | quicklook.ThumbnailsAgent     | Quick Look thumbnail-generation service                                                      | if-unused                      |
| 336 | rapportd                      | CAUSED HIGH CPU USAGE IF DISABLED                                                            | NO                             |
| 337 | recentsd                      | Recent contacts and suggestions daemon                                                       | yes                            |
| 338 | relatived                     | Motion/sensor-relative tracking service                                                      | if-unused                      |
| 339 | remindd                       | Reminders database/sync daemon                                                               | if-unused                      |
| 340 | remoted                       | Apple remote services daemon; public role not fully clear                                    | no                             |
| 341 | RemoteManagementAgent         | Remote-management agent for supervised/managed devices                                       | if-unused                      |
| 342 | remotemanagementd             | Remote-management daemon for supervised/managed devices                                      | if-unused                      |
| 343 | replayd                       | ReplayKit screen-recording/broadcast daemon                                                  | if-unused                      |
| 344 | ReportMemoryException         | Reports out-of-memory events                                                                 | yes                            |
| 345 | RestoreRemoteServices.res..   | Restore support service used by host restore workflows                                       | if-unused                      |
| 346 | retimerd                      | Private Apple hardware daemon, unclear public role                                           | no                             |
| 347 | reversetemplated              | Private Apple daemon, unclear public role                                                    | no                             |
| 348 | revisiond                     | Document revision/versioning support service                                                 | if-unused                      |
| 349 | routined                      | Significant locations and routine-learning daemon                                            | yes                            |
| 350 | rtcreportingd                 | SIP servers for FaceTime and Wi-Fi Calling                                                   | yes                            |
| 351 | runningboardd                 | Process lifecycle and resource-management daemon                                             | NO                             |
| 352 | Safari.History                | Safari history support service                                                               | if-unused                      |
| 353 | Safari.passwordbreachd        | Safari password-breach checking service                                                      | yes                            |
| 354 | Safari.SafeBrowsing.Service   | Safari fraudulent-website check service                                                      | yes                            |
| 355 | SafariBookmarksSyncAgent      | Safari bookmarks sync agent                                                                  | if-unused                      |
| 356 | safarifetcherd                | Safari Reading List offline fetcher                                                          | if-unused                      |
| 357 | safetyalertsd                 | Safety alerts service, including Emergency SOS/crash alerts                                  | NO                             |
| 358 | SCHelper                      | SystemConfiguration helper                                                                   | no                             |
| 359 | screensharingserver           | Screen-sharing / remote-screen service                                                       | if-unused                      |
| 360 | ScreenTimeAgent               | Screen Time tracking agent                                                                   | if-unused                      |
| 361 | scrod                         | Accessibility screen-recognition / OCR daemon                                                | if-unused                      |
| 362 | SecureBackupDaemon            | Secure backup support daemon                                                                 | no                             |
| 363 | security.CircleJoinRequested  | iCloud Keychain trust-circle approval service                                                | if-unused                      |
| 364 | security.cloudkeychainproxy3  | iCloud Keychain proxy daemon                                                                 | if-unused                      |
| 365 | security.cryptexd             | Cryptex and Rapid Security Response management                                               | NO                             |
| 366 | security.swcagent             | Shared Web Credentials agent                                                                 | no                             |
| 367 | securityd                     | Core security and authorization daemon                                                       | NO                             |
| 368 | securityuploadd               | Security telemetry / report upload daemon                                                    | yes                            |
| 369 | seld                          | Secure Element daemon for Apple Pay and related operations                                   | if-unused                      |
| 370 | SensorKitALSHelper            | SensorKit ambient-light helper                                                               | yes                            |
| 371 | sensorkitd                    | SensorKit daemon for approved sensor APIs                                                    | yes                            |
| 372 | SepUpdateTimer                | Secure Enclave update scheduling task                                                        | no                             |
| 373 | seserviced                    | Secure Element services daemon                                                               | if-unused                      |
| 374 | sharingd                      | disabling disables the Share sheet                                                           | NO                             |
| 375 | shazamd                       | Music-recognition daemon for Shazam integration                                              | if-unused                      |
| 376 | sidecar-relay                 | Sidecar relay service                                                                        | if-unused                      |
| 377 | signpost.signpost_reporter    | Performance signpost reporting task                                                          | yes                            |
| 378 | siriactionsd                  | HIGH CPU USAGE IF DISABLED                                                                   | NO                             |
| 379 | siriinferenced                | On-device Siri inference daemon                                                              | if-unused                      |
| 380 | siriknowledged                | Siri knowledge-cache daemon                                                                  | if-unused                      |
| 381 | sirittsd                      | Siri text-to-speech daemon                                                                   | if-unused                      |
| 382 | sleepd                        | CAUSES BOOT LOOP IF DISABLED                                                                 | NO                             |
| 383 | sntpd                         | Simple Network Time Protocol daemon                                                          | no                             |
| 384 | sociallayerd                  | Shared with You / related social-content integration                                         | if-unused                      |
| 385 | softposreaderd                | Tap to Pay on iPhone / SoftPOS reader service                                                | if-unused                      |
| 386 | sosd                          | Emergency SOS service daemon                                                                 | NO                             |
| 387 | speechmodeltrainingd          | On-device speech personalization/training                                                    | yes                            |
| 388 | spindump                      | Collects spindump diagnostics                                                                | yes                            |
| 389 | splashboardd                  | Launch-screen snapshot and splash-screen management                                          | no                             |
| 390 | sportsd                       | Sports data background service for Apple apps                                                | yes                            |
| 391 | Spotlight.IndexAgent          | Spotlight indexing agent                                                                     | no                             |
| 392 | spotlightknowledged           | Spotlight knowledge integration daemon                                                       | if-unused                      |
| 393 | SpringBoard                   | Primary iOS home screen and app UI manager                                                   | NO                             |
| 394 | StatusKitAgent                | Focus status sharing agent                                                                   | if-unused                      |
| 395 | storagedatad                  | Storage accounting and iPhone Storage calculations                                           | yes                            |
| 396 | storagekitd                   | Storage framework daemon                                                                     | no                             |
| 397 | storebookkeeperd              | Store-related bookkeeping such as play-state/metadata                                        | if-unused                      |
| 398 | storekitd                     | StoreKit and in-app purchase daemon                                                          | no                             |
| 399 | streaming_zip_conduit         | Streamed archive conduit used by install/host tooling                                        | if-unused                      |
| 400 | subridged                     | Apple Watch bridge service                                                                   | if-unused                      |
| 401 | suggestd                      | Suggestions daemon for Mail/Messages/proactive features                                      | if-unused                      |
| 402 | swcd                          | Shared Web Credentials and associated-domains daemon                                         | no                             |
| 403 | symptomsd                     | Network diagnostics and symptom-detection daemon                                             | yes                            |
| 404 | symptomsd-diag                | Detailed network diagnostics task                                                            | yes                            |
| 405 | symptomsd.helper              | Helper for network diagnostics and symptom reporting                                         | yes                            |
| 406 | synapse.contentlinkingd       | Cross-app content-linking helper; public role unclear                                        | no                             |
| 407 | SyncAgent                     | Legacy sync agent for media/content sync                                                     | if-unused                      |
| 408 | syncdefaultsd                 | Syncs some defaults/preferences via iCloud                                                   | if-unused                      |
| 409 | sysdiagnose                   | System diagnostics collection task                                                           | yes                            |
| 410 | sysdiagnose.darwinos          | Low-level Darwin OS diagnostics task                                                         | yes                            |
| 411 | sysdiagnose_helper            | Helper for system diagnostics collection                                                     | yes                            |
| 412 | systemstats.microstackshot..  | Periodic microstackshot task for performance/power stats                                     | NO                             |
| 413 | tailspind                     | Collects performance-degradation diagnostics                                                 | yes                            |
| 414 | tccd                          | Privacy permissions daemon                                                                   | NO                             |
| 415 | telephonyutilities..          | Call services daemon for CallKit, phone calls, and VoIP                                      | NO                             |
| 416 | terminusd                     | Private Apple network-path daemon, unclear public role                                       | no                             |
| 417 | TextInput.kbd                 | Keyboard text-input service                                                                  | NO                             |
| 418 | thermalmonitord               | Thermal monitoring and throttling daemon                                                     | NO                             |
| 419 | ThreadCommissionerService     | Thread-network commissioner service for smart-home accessories                               | if-unused                      |
| 420 | timed                         | System clock and time-maintenance daemon                                                     | NO                             |
| 421 | timesync.audioclocksyncd      | Audio clock-sync service                                                                     | if-unused                      |
| 422 | timezoneupdates.tzd           | Time-zone data update daemon                                                                 | no                             |
| 423 | touchsetupd                   | Device-to-device setup and transfer helper                                                   | if-unused                      |
| 424 | translationd                  | System translation framework daemon                                                          | if-unused                      |
| 425 | transparencyd                 | Apple transparency verification daemon for security features                                 | no                             |
| 426 | transparencyStaticKey         | Static key/support component for transparency services                                       | no                             |
| 427 | triald                        | Feature-flag and experimentation daemon                                                      | yes                            |
| 428 | trustd                        | Certificate trust-evaluation daemon                                                          | NO                             |
| 429 | tvremoted                     | Apple TV Remote support daemon                                                               | if-unused                      |
| 430 | tzlinkd                       | Private time-zone helper daemon                                                              | no                             |
| 431 | uikit.eyedropperd             | UIKit color-picker / eyedropper support                                                      | yes                            |
| 432 | UsageTrackingAgent            | Usage-tracking agent for Screen Time / related analytics                                     | if-unused                      |
| 433 | usb.networking.addNetwork..   | USB networking interface setup task                                                          | if-unused                      |
| 434 | usbsmartcardreaderd           | USB smart-card reader daemon                                                                 | if-unused                      |
| 435 | UserEventAgent-System         | System user-event scheduling and launch agent                                                | NO                             |
| 436 | videosubscriptionsd           | TV provider / video-subscription single sign-on daemon                                       | if-unused                      |
| 437 | voicemail.vmd                 | Visual Voicemail daemon                                                                      | if-unused                      |
| 438 | voicememod                    | Voice Memos background support daemon                                                        | if-unused                      |
| 439 | VoiceOverTouch                | VoiceOver touch-interaction support daemon                                                   | if-unused                      |
| 440 | watchdogd                     | System watchdog daemon                                                                       | NO                             |
| 441 | watchlistd                    | TV app watchlist / Up Next sync daemon                                                       | if-unused                      |
| 442 | watchpresenced                | Apple Watch proximity / presence detection daemon                                            | if-unused                      |
| 443 | wcd                           | WatchConnectivity daemon for Apple Watch apps                                                | if-unused                      |
| 444 | weatherd                      | Weather framework and weather-data service                                                   | if-unused                      |
| 445 | WebBookmarks.webbook..        | Web bookmarks daemon used by Safari and related apps                                         | if-unused                      |
| 446 | webinspectord                 | WebKit remote-inspection daemon                                                              | if-unused                      |
| 447 | webkit.adattributiond         | WebKit ad attribution / Private Click Measurement daemon                                     | yes                            |
| 448 | webkit.webpushd               | WebKit web-push notifications daemon                                                         | if-unused                      |
| 449 | wifi.hostapd                  | Wi‑Fi hotspot access-point daemon for Personal Hotspot                                       | if-unused                      |
| 450 | wifi.wapic                    | WAPI Wi‑Fi security support daemon                                                           | yes                            |
| 451 | wifianalyticsd                | Wi‑Fi analytics and telemetry daemon                                                         | yes                            |
| 452 | wifid                         | Core Wi‑Fi network-management daemon                                                         | no                             |
| 453 | WiFiFirmwareLoader            | Wi‑Fi firmware loader service                                                                | no                             |
| 454 | wifip2pd                      | Peer-to-peer Wi‑Fi daemon used by AirDrop/AirPlay                                            | if-unused                      |
| 455 | wifivelocityd                 | Wi‑Fi diagnostics and measurement daemon                                                     | yes                            |
| 456 | WirelessRadioManager          | Coordinates wireless radios such as cellular/Wi‑Fi/Bluetooth                                 | no                             |
| 457 | xpc.roleaccountd              | Private Apple XPC/account-role daemon, unclear public role                                   | no                             |
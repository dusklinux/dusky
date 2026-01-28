#!/usr/bin/env bash
# ==============================================================================
#  004_pacman_reflector.sh
#  Context: Arch Linux OS (Post-Install)
#  Description: Updates mirrorlist using Reflector with a massive global fallback.
#               Designed for high availability and manual/auto execution.
# ==============================================================================

set -euo pipefail

# --- CONFIGURATION ---
readonly TARGET_FILE="/etc/pacman.d/mirrorlist"
# Default set to 'list' for global users. Change to a country name (e.g., "India") to automate.
readonly DEFAULT_COUNTRY="list"

# --- COLORS (conditional on TTY) ---
if [[ -t 1 ]]; then
    readonly G=$'\e[32m' R=$'\e[31m' Y=$'\e[33m' B=$'\e[34m' NC=$'\e[0m'
else
    readonly G="" R="" Y="" B="" NC=""
fi

# --- LOGGING HELPERS ---
log_info() { printf '%s:: %s%s\n' "$B" "$1" "$NC"; }
log_ok()   { printf '%s:: %s%s\n' "$G" "$1" "$NC"; }
log_warn() { printf '%s:: %s%s\n' "$Y" "$1" "$NC"; }
log_err()  { printf '%s!! %s%s\n' "$R" "$1" "$NC" >&2; }

# --- PRE-FLIGHT CHECKS (ELEVATION) ---
if [[ $EUID -ne 0 ]]; then
    log_warn "This script must be run as root. Elevating privileges..."
    exec sudo "$0" "$@"
fi

if ! command -v reflector &>/dev/null; then
    log_warn "Reflector not found. Installing..."
    pacman -Syu --needed --noconfirm reflector
fi

# --- DATA STORE ---
# INSTRUCTIONS: Paste the new Arch Mirrorlist content between 'RAW_MIRROR_LIST'
# get the latest mirror list from here:
# https://archlinux.org/mirrorlist/
#
read -r -d '' FALLBACK_RAW_DATA << 'RAW_MIRROR_LIST' || true
##
## Arch Linux repository mirrorlist
## Generated on 2026-01-26
##

## Worldwide
#Server = https://fastly.mirror.pkgbuild.com/$repo/os/$arch
#Server = https://geo.mirror.pkgbuild.com/$repo/os/$arch
#Server = https://ftpmirror.infania.net/mirror/archlinux/$repo/os/$arch
#Server = http://mirror.rackspace.com/archlinux/$repo/os/$arch
#Server = https://mirror.rackspace.com/archlinux/$repo/os/$arch

## Albania
#Server = https://al.arch.niranjan.co/$repo/os/$arch

## Armenia
#Server = http://mirrors.teamcloud.am/archlinux/$repo/os/$arch
#Server = https://mirrors.teamcloud.am/archlinux/$repo/os/$arch

## Australia
#Server = https://mirror.aarnet.edu.au/pub/archlinux/$repo/os/$arch
#Server = http://au.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = https://au.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = http://archlinux.mirror.digitalpacific.com.au/$repo/os/$arch
#Server = https://archlinux.mirror.digitalpacific.com.au/$repo/os/$arch
#Server = http://gsl-syd.mm.fcix.net/archlinux/$repo/os/$arch
#Server = https://gsl-syd.mm.fcix.net/archlinux/$repo/os/$arch
#Server = http://ftp.iinet.net.au/pub/archlinux/$repo/os/$arch
#Server = http://mirror.internode.on.net/pub/archlinux/$repo/os/$arch
#Server = https://au.arch.niranjan.co/$repo/os/$arch
#Server = http://syd.mirror.rackspace.com/archlinux/$repo/os/$arch
#Server = https://syd.mirror.rackspace.com/archlinux/$repo/os/$arch
#Server = http://ftp.swin.edu.au/archlinux/$repo/os/$arch

## Austria
#Server = http://mirror.alwyzon.net/archlinux/$repo/os/$arch
#Server = https://mirror.alwyzon.net/archlinux/$repo/os/$arch
#Server = http://at.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = https://at.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = http://mirror.digitalnova.at/archlinux/$repo/os/$arch
#Server = http://mirror.easyname.at/archlinux/$repo/os/$arch
#Server = https://at.arch.mirror.kescher.at/$repo/os/$arch
#Server = https://at.arch.niranjan.co/$repo/os/$arch
#Server = https://at-vie.soulharsh007.dev/archlinux/$repo/os/$arch

## Azerbaijan
#Server = http://mirror.ourhost.az/archlinux/$repo/os/$arch
#Server = https://mirror.ourhost.az/archlinux/$repo/os/$arch
#Server = http://mirror.yer.az/archlinux/$repo/os/$arch
#Server = https://mirror.yer.az/archlinux/$repo/os/$arch

## Bangladesh
#Server = http://mirror.limda.net/archlinux/$repo/os/$arch
#Server = https://mirror.limda.net/archlinux/$repo/os/$arch
#Server = http://mirror.xeonbd.com/archlinux/$repo/os/$arch
#Server = https://mirror.xeonbd.com/archlinux/$repo/os/$arch

## Belarus
#Server = http://ftp.byfly.by/pub/archlinux/$repo/os/$arch
#Server = http://mirror.datacenter.by/pub/archlinux/$repo/os/$arch

## Belgium
#Server = http://mirror.1ago.be/archlinux/$repo/os/$arch
#Server = https://mirror.1ago.be/archlinux/$repo/os/$arch
#Server = http://mirror.jonas-prz.be/$repo/os/$arch
#Server = https://mirror.jonas-prz.be/$repo/os/$arch
#Server = https://archlinux.mirror-services.net/archlinux/$repo/os/$arch
#Server = http://mirror.tiguinet.net/arch/$repo/os/$arch
#Server = https://mirror.tiguinet.net/arch/$repo/os/$arch

## Brazil
#Server = http://archlinux.c3sl.ufpr.br/$repo/os/$arch
#Server = https://archlinux.c3sl.ufpr.br/$repo/os/$arch
#Server = http://br.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = https://br.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = http://mirror.ufam.edu.br/archlinux/$repo/os/$arch
#Server = http://mirror.ufscar.br/archlinux/$repo/os/$arch
#Server = https://mirror.ufscar.br/archlinux/$repo/os/$arch
#Server = http://mirrors.ic.unicamp.br/archlinux/$repo/os/$arch
#Server = https://mirrors.ic.unicamp.br/archlinux/$repo/os/$arch

## Bulgaria
#Server = http://mirror.host.ag/archlinux/$repo/os/$arch
#Server = http://mirror.telepoint.bg/archlinux/$repo/os/$arch
#Server = https://mirror.telepoint.bg/archlinux/$repo/os/$arch
#Server = http://mirrors.uni-plovdiv.net/archlinux/$repo/os/$arch
#Server = https://mirrors.uni-plovdiv.net/archlinux/$repo/os/$arch

## Cambodia
#Server = http://mirror.sabay.com.kh/archlinux/$repo/os/$arch
#Server = https://mirror.sabay.com.kh/archlinux/$repo/os/$arch

## Canada
#Server = http://mirror.0xem.ma/arch/$repo/os/$arch
#Server = https://mirror.0xem.ma/arch/$repo/os/$arch
#Server = https://mirror.acadielinux.ca/mirror/arch/$repo/os/$arch
#Server = https://arch.mirror.winslow.cloud/$repo/os/$arch
#Server = http://ca.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = https://ca.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = http://mirror.cpsc.ucalgary.ca/mirror/archlinux.org/$repo/os/$arch
#Server = https://mirror.cpsc.ucalgary.ca/mirror/archlinux.org/$repo/os/$arch
#Server = http://mirror.csclub.uwaterloo.ca/archlinux/$repo/os/$arch
#Server = https://mirror.csclub.uwaterloo.ca/archlinux/$repo/os/$arch
#Server = http://mirror2.evolution-host.com/archlinux/$repo/os/$arch
#Server = https://mirror2.evolution-host.com/archlinux/$repo/os/$arch
#Server = https://stygian.failzero.net/mirror/archlinux/$repo/os/$arch
#Server = https://mirror.franscorack.com/archlinux/$repo/os/$arch
#Server = http://mirror.its.dal.ca/archlinux/$repo/os/$arch
#Server = http://mirror.quantum5.ca/archlinux/$repo/os/$arch
#Server = https://mirror.quantum5.ca/archlinux/$repo/os/$arch
#Server = http://muug.ca/mirror/archlinux/$repo/os/$arch
#Server = https://muug.ca/mirror/archlinux/$repo/os/$arch
#Server = http://mirrors.pablonara.com/archlinux/$repo/os/$arch
#Server = https://mirrors.pablonara.com/archlinux/$repo/os/$arch
#Server = https://upstream-1.pablonara.com/archlinux/$repo/os/$arch
#Server = http://archlinux.mirror.rafal.ca/$repo/os/$arch
#Server = http://mirror.scd31.com/arch/$repo/os/$arch
#Server = https://mirror.scd31.com/arch/$repo/os/$arch
#Server = http://mirror.xenyth.net/archlinux/$repo/os/$arch
#Server = https://mirror.xenyth.net/archlinux/$repo/os/$arch

## Chile
#Server = http://mirror.anquan.cl/archlinux/$repo/os/$arch
#Server = https://mirror.anquan.cl/archlinux/$repo/os/$arch
#Server = http://elmirror.cl/archlinux/$repo/os/$arch
#Server = https://elmirror.cl/archlinux/$repo/os/$arch
#Server = http://mirror.hnd.cl/archlinux/$repo/os/$arch
#Server = https://mirror.hnd.cl/archlinux/$repo/os/$arch
#Server = http://mirror.ufro.cl/archlinux/$repo/os/$arch
#Server = https://mirror.ufro.cl/archlinux/$repo/os/$arch

## China
#Server = http://mirrors.163.com/archlinux/$repo/os/$arch
#Server = http://mirrors.aliyun.com/archlinux/$repo/os/$arch
#Server = https://mirrors.aliyun.com/archlinux/$repo/os/$arch
#Server = http://mirrors.bfsu.edu.cn/archlinux/$repo/os/$arch
#Server = https://mirrors.bfsu.edu.cn/archlinux/$repo/os/$arch
#Server = http://mirrors.cqu.edu.cn/archlinux/$repo/os/$arch
#Server = https://mirrors.cqu.edu.cn/archlinux/$repo/os/$arch
#Server = http://mirrors.hit.edu.cn/archlinux/$repo/os/$arch
#Server = https://mirrors.hit.edu.cn/archlinux/$repo/os/$arch
#Server = http://mirrors.hust.edu.cn/archlinux/$repo/os/$arch
#Server = https://mirrors.hust.edu.cn/archlinux/$repo/os/$arch
#Server = http://mirrors.jcut.edu.cn/archlinux/$repo/os/$arch
#Server = https://mirrors.jcut.edu.cn/archlinux/$repo/os/$arch
#Server = http://mirrors.jlu.edu.cn/archlinux/$repo/os/$arch
#Server = https://mirrors.jlu.edu.cn/archlinux/$repo/os/$arch
#Server = http://mirrors.jxust.edu.cn/archlinux/$repo/os/$arch
#Server = https://mirrors.jxust.edu.cn/archlinux/$repo/os/$arch
#Server = http://mirror.lzu.edu.cn/archlinux/$repo/os/$arch
#Server = http://mirrors.neusoft.edu.cn/archlinux/$repo/os/$arch
#Server = https://mirrors.neusoft.edu.cn/archlinux/$repo/os/$arch
#Server = http://mirrors.nju.edu.cn/archlinux/$repo/os/$arch
#Server = https://mirrors.nju.edu.cn/archlinux/$repo/os/$arch
#Server = http://mirror.nyist.edu.cn/archlinux/$repo/os/$arch
#Server = https://mirror.nyist.edu.cn/archlinux/$repo/os/$arch
#Server = https://mirrors.qlu.edu.cn/archlinux/$repo/os/$arch
#Server = http://mirrors.shanghaitech.edu.cn/archlinux/$repo/os/$arch
#Server = https://mirrors.shanghaitech.edu.cn/archlinux/$repo/os/$arch
#Server = https://mirrors.sjtug.sjtu.edu.cn/archlinux/$repo/os/$arch
#Server = http://mirrors.tuna.tsinghua.edu.cn/archlinux/$repo/os/$arch
#Server = https://mirrors.tuna.tsinghua.edu.cn/archlinux/$repo/os/$arch
#Server = http://mirrors.ustc.edu.cn/archlinux/$repo/os/$arch
#Server = https://mirrors.ustc.edu.cn/archlinux/$repo/os/$arch
#Server = http://mirrors.wsyu.edu.cn/archlinux/$repo/os/$arch
#Server = https://mirrors.wsyu.edu.cn/archlinux/$repo/os/$arch
#Server = https://mirrors.xjtu.edu.cn/archlinux/$repo/os/$arch
#Server = http://mirrors.zju.edu.cn/archlinux/$repo/os/$arch

## Colombia
#Server = http://mirrors.atlas.net.co/archlinux/$repo/os/$arch
#Server = https://mirrors.atlas.net.co/archlinux/$repo/os/$arch
#Server = http://edgeuno-bog2.mm.fcix.net/archlinux/$repo/os/$arch
#Server = https://edgeuno-bog2.mm.fcix.net/archlinux/$repo/os/$arch
#Server = http://mirrors.udenar.edu.co/archlinux/$repo/os/$arch

## Croatia
#Server = http://archlinux.iskon.hr/$repo/os/$arch

## Czechia
#Server = http://mirror.dkm.cz/archlinux/$repo/os/$arch
#Server = https://mirror.dkm.cz/archlinux/$repo/os/$arch
#Server = http://ftp.fi.muni.cz/pub/linux/arch/$repo/os/$arch
#Server = http://ftp.linux.cz/pub/linux/arch/$repo/os/$arch
#Server = http://gluttony.sin.cvut.cz/arch/$repo/os/$arch
#Server = https://gluttony.sin.cvut.cz/arch/$repo/os/$arch
#Server = http://mirror.it4i.cz/arch/$repo/os/$arch
#Server = https://mirror.it4i.cz/arch/$repo/os/$arch
#Server = http://archlinux.nic.cz/archlinux/$repo/os/$arch
#Server = https://archlinux.nic.cz/archlinux/$repo/os/$arch
#Server = http://ftp.sh.cvut.cz/arch/$repo/os/$arch
#Server = https://ftp.sh.cvut.cz/arch/$repo/os/$arch
#Server = http://mirror.vpsfree.cz/archlinux/$repo/os/$arch

## Denmark
#Server = http://mirrors.dotsrc.org/archlinux/$repo/os/$arch
#Server = https://mirrors.dotsrc.org/archlinux/$repo/os/$arch
#Server = http://mirror.group.one/archlinux/$repo/os/$arch
#Server = https://mirror.group.one/archlinux/$repo/os/$arch
#Server = https://mirror.it-privat.dk/arch/$repo/os/$arch

## Ecuador
#Server = http://mirror.cedia.org.ec/archlinux/$repo/os/$arch
#Server = https://mirror.linux.ec/archlinux/$repo/os/$arch

## Estonia
#Server = http://mirror.cspacehostings.com/archlinux/$repo/os/$arch
#Server = https://mirror.cspacehostings.com/archlinux/$repo/os/$arch
#Server = http://repo.br.ee/arch/$repo/os/$arch
#Server = https://repo.br.ee/arch/$repo/os/$arch
#Server = http://mirrors.xtom.ee/archlinux/$repo/os/$arch
#Server = https://mirrors.xtom.ee/archlinux/$repo/os/$arch

## Finland
#Server = https://archlinux.doridian.net/$repo/os/$arch
#Server = http://cdnmirror.com/archlinux/$repo/os/$arch
#Server = https://cdnmirror.com/archlinux/$repo/os/$arch
#Server = http://arch.mirror.far.fi/$repo/os/$arch
#Server = http://mirror.5i.fi/archlinux/$repo/os/$arch
#Server = https://mirror.5i.fi/archlinux/$repo/os/$arch
#Server = https://fi.arch.niranjan.co/$repo/os/$arch
#Server = https://mirror.srv.fail/archlinux/$repo/os/$arch
#Server = http://mirror.wuki.li/archlinux/$repo/os/$arch
#Server = https://mirror.wuki.li/archlinux/$repo/os/$arch
#Server = http://arch.yhtez.xyz/$repo/os/$arch
#Server = https://arch.yhtez.xyz/$repo/os/$arch

## France
#Server = http://mirror.archlinux.ikoula.com/archlinux/$repo/os/$arch
#Server = https://elda.asgardius.company/archlinux/$repo/os/$arch
#Server = http://mirror.bakertelekom.fr/Arch/$repo/os/$arch
#Server = https://mirror.bakertelekom.fr/Arch/$repo/os/$arch
#Server = http://fr.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = https://fr.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = https://mirror.cosmiclinux.com/archlinux/$repo/os/$arch
#Server = http://mirror.cyberbits.eu/archlinux/$repo/os/$arch
#Server = https://mirror.cyberbits.eu/archlinux/$repo/os/$arch
#Server = http://archlinux.datagr.am/$repo/os/$arch
#Server = https://mirrors.eric.ovh/arch/$repo/os/$arch
#Server = http://mirrors.gandi.net/archlinux/$repo/os/$arch
#Server = https://mirrors.gandi.net/archlinux/$repo/os/$arch
#Server = http://archmirror.hogwarts.fr/$repo/os/$arch
#Server = https://archmirror.hogwarts.fr/$repo/os/$arch
#Server = http://mirror.its-tps.fr/archlinux/$repo/os/$arch
#Server = https://mirror.its-tps.fr/archlinux/$repo/os/$arch
#Server = https://mirrors.jtremesay.org/archlinux/$repo/os/$arch
#Server = http://mirror.lastmikoi.net/archlinux/$repo/os/$arch
#Server = http://archlinux.mailtunnel.eu/$repo/os/$arch
#Server = https://archlinux.mailtunnel.eu/$repo/os/$arch
#Server = http://f.matthieul.dev/mirror/archlinux/$repo/os/$arch
#Server = https://f.matthieul.dev/mirror/archlinux/$repo/os/$arch
#Server = http://mir.archlinux.fr/$repo/os/$arch
#Server = http://mirror.oldsql.cc/archlinux/$repo/os/$arch
#Server = https://mirror.oldsql.cc/archlinux/$repo/os/$arch
#Server = http://archlinux.mirrors.ovh.net/archlinux/$repo/os/$arch
#Server = https://archlinux.mirrors.ovh.net/archlinux/$repo/os/$arch
#Server = http://mirror.peeres-telecom.fr/archlinux/$repo/os/$arch
#Server = https://mirror.peeres-telecom.fr/archlinux/$repo/os/$arch
#Server = http://mirror.rznet.fr/archlinux/$repo/os/$arch
#Server = https://mirror.rznet.fr/archlinux/$repo/os/$arch
#Server = http://fr.mirror.shibe.party/archlinux/$repo/os/$arch
#Server = https://fr.mirror.shibe.party/archlinux/$repo/os/$arch
#Server = https://mirror.smayzy.ovh/archlinux/$repo/os/$arch
#Server = http://arch.syxpi.fr/arch/$repo/os/$arch
#Server = https://arch.syxpi.fr/arch/$repo/os/$arch
#Server = https://mirror.thekinrar.fr/archlinux/$repo/os/$arch
#Server = http://mirror.theo546.fr/archlinux/$repo/os/$arch
#Server = https://mirror.theo546.fr/archlinux/$repo/os/$arch
#Server = http://mirror.trap.moe/archlinux/$repo/os/$arch
#Server = https://mirror.trap.moe/archlinux/$repo/os/$arch
#Server = http://ftp.u-strasbg.fr/linux/distributions/archlinux/$repo/os/$arch
#Server = https://mirror.wormhole.eu/archlinux/$repo/os/$arch
#Server = http://arch.yourlabs.org/$repo/os/$arch
#Server = https://arch.yourlabs.org/$repo/os/$arch

## Georgia
#Server = http://archlinux.grena.ge/$repo/os/$arch
#Server = https://archlinux.grena.ge/$repo/os/$arch

## Germany
#Server = http://mirror.23m.com/archlinux/$repo/os/$arch
#Server = https://mirror.23m.com/archlinux/$repo/os/$arch
#Server = http://ftp.agdsn.de/pub/mirrors/archlinux/$repo/os/$arch
#Server = https://ftp.agdsn.de/pub/mirrors/archlinux/$repo/os/$arch
#Server = http://mirrors.aminvakil.com/archlinux/$repo/os/$arch
#Server = https://mirrors.aminvakil.com/archlinux/$repo/os/$arch
#Server = http://artfiles.org/archlinux.org/$repo/os/$arch
#Server = https://mirror.bethselamin.de/$repo/os/$arch
#Server = https://de.repo.c48.uk/arch/$repo/os/$arch
#Server = http://de.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = https://de.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = http://mirror.clientvps.com/archlinux/$repo/os/$arch
#Server = https://mirror.clientvps.com/archlinux/$repo/os/$arch
#Server = http://mirror.cmt.de/archlinux/$repo/os/$arch
#Server = https://mirror.cmt.de/archlinux/$repo/os/$arch
#Server = http://os.codefionn.eu/archlinux/$repo/os/$arch
#Server = https://os.codefionn.eu/archlinux/$repo/os/$arch
#Server = http://mirror-de-1.cutie.dating/archlinux/$repo/os/$arch
#Server = https://mirror-de-1.cutie.dating/archlinux/$repo/os/$arch
#Server = http://mirror.diyarciftci.xyz/archlinux/$repo/os/$arch
#Server = https://mirror.diyarciftci.xyz/archlinux/$repo/os/$arch
#Server = https://mirror.dogado.de/archlinux/$repo/os/$arch
#Server = http://ftp.fau.de/archlinux/$repo/os/$arch
#Server = https://ftp.fau.de/archlinux/$repo/os/$arch
#Server = https://pkg.fef.moe/archlinux/$repo/os/$arch
#Server = https://dist-mirror.fem.tu-ilmenau.de/archlinux/$repo/os/$arch
#Server = https://berlin.mirror.pkgbuild.com/$repo/os/$arch
#Server = https://frankfurt.mirror.pkgbuild.com/$repo/os/$arch
#Server = http://ftp.gwdg.de/pub/linux/archlinux/$repo/os/$arch
#Server = https://files.hadiko.de/pub/dists/arch/$repo/os/$arch
#Server = http://ftp.hosteurope.de/mirror/ftp.archlinux.org/$repo/os/$arch
#Server = http://ftp-stud.hs-esslingen.de/pub/Mirrors/archlinux/$repo/os/$arch
#Server = http://mirror.hugo-betrugo.de/archlinux/$repo/os/$arch
#Server = https://mirror.hugo-betrugo.de/archlinux/$repo/os/$arch
#Server = http://mirror.informatik.tu-freiberg.de/arch/$repo/os/$arch
#Server = https://mirror.informatik.tu-freiberg.de/arch/$repo/os/$arch
#Server = http://mirror.as20647.net/archlinux/$repo/os/$arch
#Server = http://mirror.ipb.de/archlinux/$repo/os/$arch
#Server = https://mirror.as20647.net/archlinux/$repo/os/$arch
#Server = https://mirror.ipb.de/archlinux/$repo/os/$arch
#Server = http://archlinux.mirror.iphh.net/$repo/os/$arch
#Server = http://mirrors.janbruckner.de/archlinux/$repo/os/$arch
#Server = https://mirrors.janbruckner.de/archlinux/$repo/os/$arch
#Server = http://arch.jensgutermuth.de/$repo/os/$arch
#Server = https://arch.jensgutermuth.de/$repo/os/$arch
#Server = https://de.arch.mirror.kescher.at/$repo/os/$arch
#Server = http://mirror.kumi.systems/archlinux/$repo/os/$arch
#Server = https://mirror.kumi.systems/archlinux/$repo/os/$arch
#Server = http://mirror.fra10.de.leaseweb.net/archlinux/$repo/os/$arch
#Server = https://mirror.fra10.de.leaseweb.net/archlinux/$repo/os/$arch
#Server = http://mirror.metalgamer.eu/archlinux/$repo/os/$arch
#Server = https://mirror.metalgamer.eu/archlinux/$repo/os/$arch
#Server = http://mirror.lcarilla.de/archlinux/$repo/os/$arch
#Server = https://mirror.lcarilla.de/archlinux/$repo/os/$arch
#Server = http://mirror.moson.org/arch/$repo/os/$arch
#Server = https://mirror.moson.org/arch/$repo/os/$arch
#Server = http://mirrors.n-ix.net/archlinux/$repo/os/$arch
#Server = https://mirrors.n-ix.net/archlinux/$repo/os/$arch
#Server = http://mirror.netcologne.de/archlinux/$repo/os/$arch
#Server = https://mirror.netcologne.de/archlinux/$repo/os/$arch
#Server = https://de.arch.niranjan.co/$repo/os/$arch
#Server = http://mirrors.niyawe.de/archlinux/$repo/os/$arch
#Server = https://mirrors.niyawe.de/archlinux/$repo/os/$arch
#Server = http://packages.oth-regensburg.de/archlinux/$repo/os/$arch
#Server = https://packages.oth-regensburg.de/archlinux/$repo/os/$arch
#Server = http://arch.owochle.app/$repo/os/$arch
#Server = https://arch.owochle.app/$repo/os/$arch
#Server = http://mirror.pagenotfound.de/archlinux/$repo/os/$arch
#Server = https://mirror.pagenotfound.de/archlinux/$repo/os/$arch
#Server = http://arch.phinau.de/$repo/os/$arch
#Server = https://arch.phinau.de/$repo/os/$arch
#Server = https://mirror.pseudoform.org/$repo/os/$arch
#Server = http://mirrors.purring.online/arch/$repo/os/$arch
#Server = https://mirrors.purring.online/arch/$repo/os/$arch
#Server = https://archlinux.richard-neumann.de/$repo/os/$arch
#Server = http://ftp.halifax.rwth-aachen.de/archlinux/$repo/os/$arch
#Server = https://ftp.halifax.rwth-aachen.de/archlinux/$repo/os/$arch
#Server = http://linux.rz.rub.de/archlinux/$repo/os/$arch
#Server = http://mirror.selfnet.de/archlinux/$repo/os/$arch
#Server = https://mirror.selfnet.de/archlinux/$repo/os/$arch
#Server = http://de.mirror.shibe.party/archlinux/$repo/os/$arch
#Server = https://de.mirror.shibe.party/archlinux/$repo/os/$arch
#Server = https://de-nue.soulharsh007.dev/archlinux/$repo/os/$arch
#Server = http://ftp.spline.inf.fu-berlin.de/mirrors/archlinux/$repo/os/$arch
#Server = https://ftp.spline.inf.fu-berlin.de/mirrors/archlinux/$repo/os/$arch
#Server = http://mirror.sunred.org/archlinux/$repo/os/$arch
#Server = https://mirror.sunred.org/archlinux/$repo/os/$arch
#Server = http://archlinux.thaller.ws/$repo/os/$arch
#Server = https://archlinux.thaller.ws/$repo/os/$arch
#Server = http://arch.mirror.cloud.thatcyberlynx.de/$repo/os/$arch
#Server = https://arch.mirror.cloud.thatcyberlynx.de/$repo/os/$arch
#Server = https://mirror.thereisno.page/archlinux/$repo/os/$arch
#Server = http://ftp.tu-chemnitz.de/pub/linux/archlinux/$repo/os/$arch
#Server = http://mirror.ubrco.de/archlinux/$repo/os/$arch
#Server = https://mirror.ubrco.de/archlinux/$repo/os/$arch
#Server = http://ftp.uni-bayreuth.de/linux/archlinux/$repo/os/$arch
#Server = http://ftp.uni-hannover.de/archlinux/$repo/os/$arch
#Server = http://ftp.uni-kl.de/pub/linux/archlinux/$repo/os/$arch
#Server = https://arch.unixpeople.org/$repo/os/$arch
#Server = http://mirror.wtnet.de/archlinux/$repo/os/$arch
#Server = https://mirror.wtnet.de/archlinux/$repo/os/$arch
#Server = http://mirrors.xtom.de/archlinux/$repo/os/$arch
#Server = https://mirrors.xtom.de/archlinux/$repo/os/$arch

## Greece
#Server = http://ftp.cc.uoc.gr/mirrors/linux/archlinux/$repo/os/$arch
#Server = https://repo.greeklug.gr/data/pub/linux/archlinux/$repo/os/$arch
#Server = http://ftp.otenet.gr/linux/archlinux/$repo/os/$arch

## Hong Kong
#Server = https://hk.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = http://mirror-hk.koddos.net/archlinux/$repo/os/$arch
#Server = https://mirror-hk.koddos.net/archlinux/$repo/os/$arch
#Server = http://hkg.mirror.rackspace.com/archlinux/$repo/os/$arch
#Server = https://hkg.mirror.rackspace.com/archlinux/$repo/os/$arch
#Server = https://arch-mirror.wtako.net/$repo/os/$arch
#Server = http://mirror.xtom.com.hk/archlinux/$repo/os/$arch
#Server = https://mirror.xtom.com.hk/archlinux/$repo/os/$arch

## Hungary
#Server = https://ftp.ek-cer.hu/pub/mirrors/ftp.archlinux.org/$repo/os/$arch
#Server = http://hu.mirror.frigyes.dev/archlinux/$repo/os/$arch
#Server = https://hu.mirror.frigyes.dev/archlinux/$repo/os/$arch
#Server = http://archmirror.hbit.sztaki.hu/archlinux/$repo/os/$arch

## Iceland
#Server = http://is.mirror.flokinet.net/archlinux/$repo/os/$arch
#Server = https://is.mirror.flokinet.net/archlinux/$repo/os/$arch

## India
#Server = http://mirror.4v1.in/archlinux/$repo/os/$arch
#Server = https://mirror.4v1.in/archlinux/$repo/os/$arch
#Server = https://mirrors.abhy.me/archlinux/$repo/os/$arch
#Server = https://mirror.del2.albony.in/archlinux/$repo/os/$arch
#Server = https://mirror.maa.albony.in/archlinux/$repo/os/$arch
#Server = http://in.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = https://in.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = http://in-mirror.garudalinux.org/archlinux/$repo/os/$arch
#Server = https://in-mirror.garudalinux.org/archlinux/$repo/os/$arch
#Server = https://archlinux.kushwanthreddy.com/$repo/os/$arch
#Server = https://in.arch.niranjan.co/$repo/os/$arch
#Server = http://mirrors.nxtgen.com/archlinux-mirror/$repo/os/$arch
#Server = https://mirrors.nxtgen.com/archlinux-mirror/$repo/os/$arch
#Server = http://mirror.sahil.world/archlinux/$repo/os/$arch
#Server = https://mirror.sahil.world/archlinux/$repo/os/$arch
#Server = http://mirrors.saswata.cc/archlinux/$repo/os/$arch
#Server = https://mirrors.saswata.cc/archlinux/$repo/os/$arch

## Indonesia
#Server = http://mirror.citrahost.com/archlinux/$repo/os/$arch
#Server = https://mirror.citrahost.com/archlinux/$repo/os/$arch
#Server = http://mirror.gi.co.id/archlinux/$repo/os/$arch
#Server = https://mirror.gi.co.id/archlinux/$repo/os/$arch
#Server = http://kebo.pens.ac.id/archlinux/$repo/os/$arch
#Server = http://mirror.ditatompel.com/archlinux/$repo/os/$arch
#Server = https://mirror.ditatompel.com/archlinux/$repo/os/$arch
#Server = http://mirror.papua.go.id/archlinux/$repo/os/$arch
#Server = https://mirror.papua.go.id/archlinux/$repo/os/$arch
#Server = http://mirror.repository.id/archlinux/$repo/os/$arch
#Server = https://mirror.repository.id/archlinux/$repo/os/$arch
#Server = https://kacabenggala.uny.ac.id/archlinux/$repo/os/$arch

## Iran
#Server = http://mirror.arvancloud.ir/archlinux/$repo/os/$arch
#Server = https://mirror.arvancloud.ir/archlinux/$repo/os/$arch
#Server = http://repo.iut.ac.ir/repo/archlinux/$repo/os/$arch
#Server = http://mirror.mobinhost.com/archlinux/$repo/os/$arch
#Server = https://mirror.mobinhost.com/archlinux/$repo/os/$arch

## Israel
#Server = http://archlinux.interhost.co.il/$repo/os/$arch
#Server = https://archlinux.interhost.co.il/$repo/os/$arch
#Server = http://mirror.isoc.org.il/pub/archlinux/$repo/os/$arch
#Server = https://mirror.isoc.org.il/pub/archlinux/$repo/os/$arch

## Italy
#Server = http://it.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = https://it.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = http://archlinux.mirror.garr.it/archlinux/$repo/os/$arch
#Server = https://arch.mirror.hyperbit.it/$repo/os/$arch
#Server = http://mala-arch-server.eu/$repo/os/$arch
#Server = https://mala-arch-server.eu/$repo/os/$arch
#Server = http://archlinux.mirror.server24.net/$repo/os/$arch
#Server = https://archlinux.mirror.server24.net/$repo/os/$arch

## Japan
#Server = http://mirror.aria-on-the-planet.es/archlinux/$repo/os/$arch
#Server = https://mirror.aria-on-the-planet.es/archlinux/$repo/os/$arch
#Server = http://mirrors.cat.net/archlinux/$repo/os/$arch
#Server = https://mirrors.cat.net/archlinux/$repo/os/$arch
#Server = http://jp.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = https://jp.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = http://ftp.tsukuba.wide.ad.jp/Linux/archlinux/$repo/os/$arch
#Server = http://ftp.jaist.ac.jp/pub/Linux/ArchLinux/$repo/os/$arch
#Server = https://ftp.jaist.ac.jp/pub/Linux/ArchLinux/$repo/os/$arch
#Server = http://www.miraa.jp/archlinux/$repo/os/$arch
#Server = https://www.miraa.jp/archlinux/$repo/os/$arch
#Server = http://mirror.rain.ne.jp/archlinux/$repo/os/$arch
#Server = https://mirror.rain.ne.jp/archlinux/$repo/os/$arch

## Kazakhstan
#Server = http://mirror.ps.kz/archlinux/$repo/os/$arch
#Server = https://mirror.ps.kz/archlinux/$repo/os/$arch

## Kenya
#Server = http://archlinux.mirror.liquidtelecom.com/$repo/os/$arch
#Server = https://archlinux.mirror.liquidtelecom.com/$repo/os/$arch

## Latvia
#Server = http://ftp.linux.edu.lv/archlinux/$repo/os/$arch
#Server = https://ftp.linux.edu.lv/archlinux/$repo/os/$arch
#Server = http://archlinux.koyanet.lv/archlinux/$repo/os/$arch
#Server = https://archlinux.koyanet.lv/archlinux/$repo/os/$arch

## Lithuania
#Server = http://mirrors.atviras.lt/archlinux/$repo/os/$arch
#Server = https://mirrors.atviras.lt/archlinux/$repo/os/$arch
#Server = http://mirror.sinirlan.net/archlinux/$repo/os/$arch
#Server = https://mirror.sinirlan.net/archlinux/$repo/os/$arch

## Luxembourg
#Server = http://arch-lux.spirex.me/$repo/os/$arch
#Server = https://arch-lux.spirex.me/$repo/os/$arch

## Malaysia
#Server = https://mirror.mrleong.net/archlinux/$repo/os/$arch

## Mauritius
#Server = http://archlinux-mirror.cloud.mu/$repo/os/$arch
#Server = https://archlinux-mirror.cloud.mu/$repo/os/$arch

## Mexico
#Server = http://lidsol.fi-b.unam.mx/archlinux/$repo/os/$arch
#Server = https://lidsol.fi-b.unam.mx/archlinux/$repo/os/$arch
#Server = https://arch.jsc.mx/$repo/os/$arch

## Moldova
#Server = http://md.mirrors.hacktegic.com/archlinux/$repo/os/$arch
#Server = https://md.mirrors.hacktegic.com/archlinux/$repo/os/$arch
#Server = http://mirror.hosthink.net/arch/$repo/os/$arch
#Server = https://mirror.hosthink.net/arch/$repo/os/$arch
#Server = http://mirror.ihost.md/archlinux/$repo/os/$arch
#Server = https://mirror.ihost.md/archlinux/$repo/os/$arch
#Server = http://mirror.mangohost.net/archlinux/$repo/os/$arch
#Server = https://mirror.mangohost.net/archlinux/$repo/os/$arch
#Server = https://md.arch.niranjan.co/$repo/os/$arch

## Morocco
#Server = http://mirror.abderraziq.com/archlinux/$repo/os/$arch
#Server = https://mirror.abderraziq.com/archlinux/$repo/os/$arch

## Nepal
#Server = http://mirrors.nepalicloud.com/archlinux/$repo/os/$arch
#Server = https://mirrors.nepalicloud.com/archlinux/$repo/os/$arch

## Netherlands
#Server = http://ams.nl.mirrors.bjg.at/arch/$repo/os/$arch
#Server = https://ams.nl.mirrors.bjg.at/arch/$repo/os/$arch
#Server = http://mirror.bouwhuis.network/archlinux/$repo/os/$arch
#Server = https://mirror.bouwhuis.network/archlinux/$repo/os/$arch
#Server = http://mirror.nl.cdn-perfprod.com/archlinux/$repo/os/$arch
#Server = https://mirror.nl.cdn-perfprod.com/archlinux/$repo/os/$arch
#Server = http://nl.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = https://nl.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = http://mirror.cj2.nl/archlinux/$repo/os/$arch
#Server = https://mirror.cj2.nl/archlinux/$repo/os/$arch
#Server = http://mirrors.evoluso.com/archlinux/$repo/os/$arch
#Server = http://nl.mirror.flokinet.net/archlinux/$repo/os/$arch
#Server = https://nl.mirror.flokinet.net/archlinux/$repo/os/$arch
#Server = https://mirror.iusearchbtw.nl/$repo/os/$arch
#Server = http://mirror.koddos.net/archlinux/$repo/os/$arch
#Server = https://mirror.koddos.net/archlinux/$repo/os/$arch
#Server = http://arch.mirrors.lavatech.top/$repo/os/$arch
#Server = https://arch.mirrors.lavatech.top/$repo/os/$arch
#Server = http://mirror.ams1.nl.leaseweb.net/archlinux/$repo/os/$arch
#Server = https://mirror.ams1.nl.leaseweb.net/archlinux/$repo/os/$arch
#Server = http://archlinux.mirror.liteserver.nl/$repo/os/$arch
#Server = https://archlinux.mirror.liteserver.nl/$repo/os/$arch
#Server = http://mirror.lyrahosting.com/archlinux/$repo/os/$arch
#Server = https://mirror.lyrahosting.com/archlinux/$repo/os/$arch
#Server = http://mirror.mijn.host/archlinux/$repo/os/$arch
#Server = https://mirror.mijn.host/archlinux/$repo/os/$arch
#Server = https://nl.arch.niranjan.co/$repo/os/$arch
#Server = http://ftp.nluug.nl/os/Linux/distr/archlinux/$repo/os/$arch
#Server = http://mirror.nyaa.vc/archlinux/$repo/os/$arch
#Server = https://mirror.nyaa.vc/archlinux/$repo/os/$arch
#Server = http://mirror.serverion.com/archlinux/$repo/os/$arch
#Server = https://mirror.serverion.com/archlinux/$repo/os/$arch
#Server = http://ftp.snt.utwente.nl/pub/os/linux/archlinux/$repo/os/$arch
#Server = http://archlinux.mirror.wearetriple.com/$repo/os/$arch
#Server = https://archlinux.mirror.wearetriple.com/$repo/os/$arch
#Server = http://mirrors.xtom.nl/archlinux/$repo/os/$arch
#Server = https://mirrors.xtom.nl/archlinux/$repo/os/$arch

## New Caledonia
#Server = http://mirror.lagoon.nc/pub/archlinux/$repo/os/$arch
#Server = http://archlinux.nautile.nc/archlinux/$repo/os/$arch
#Server = https://archlinux.nautile.nc/archlinux/$repo/os/$arch

## New Zealand
#Server = http://mirror.2degrees.nz/archlinux/$repo/os/$arch
#Server = https://mirror.2degrees.nz/archlinux/$repo/os/$arch
#Server = http://mirror.fsmg.org.nz/archlinux/$repo/os/$arch
#Server = https://mirror.fsmg.org.nz/archlinux/$repo/os/$arch
#Server = https://nz.arch.niranjan.co/$repo/os/$arch
#Server = https://archlinux.ourhome.kiwi/$repo/os/$arch

## North Macedonia
#Server = http://arch.softver.org.mk/archlinux/$repo/os/$arch
#Server = http://mirror.onevip.mk/archlinux/$repo/os/$arch
#Server = http://mirror.t-home.mk/archlinux/$repo/os/$arch
#Server = https://mirror.t-home.mk/archlinux/$repo/os/$arch

## Norway
#Server = http://mirror.archlinux.no/$repo/os/$arch
#Server = https://mirror.archlinux.no/$repo/os/$arch
#Server = http://archlinux.uib.no/$repo/os/$arch
#Server = https://archlinux.lysakermoen.com/$repo/os/$arch
#Server = http://mirror.neuf.no/archlinux/$repo/os/$arch
#Server = https://mirror.neuf.no/archlinux/$repo/os/$arch
#Server = http://mirror.terrahost.no/linux/archlinux/$repo/os/$arch

## Paraguay
#Server = http://archlinux.mirror.py/archlinux/$repo/os/$arch

## Poland
#Server = http://ftp.icm.edu.pl/pub/Linux/dist/archlinux/$repo/os/$arch
#Server = https://ftp.icm.edu.pl/pub/Linux/dist/archlinux/$repo/os/$arch
#Server = http://mirror.juniorjpdj.pl/archlinux/$repo/os/$arch
#Server = https://mirror.juniorjpdj.pl/archlinux/$repo/os/$arch
#Server = http://arch.midov.pl/arch/$repo/os/$arch
#Server = https://arch.midov.pl/arch/$repo/os/$arch
#Server = https://mirror.przekichane.pl/archlinux/$repo/os/$arch
#Server = http://ftp.psnc.pl/linux/archlinux/$repo/os/$arch
#Server = https://ftp.psnc.pl/linux/archlinux/$repo/os/$arch
#Server = http://arch.sakamoto.pl/$repo/os/$arch
#Server = https://arch.sakamoto.pl/$repo/os/$arch

## Portugal
#Server = http://mirror.barata.pt/archlinux/$repo/os/$arch
#Server = https://mirror.barata.pt/archlinux/$repo/os/$arch
#Server = http://glua.ua.pt/pub/archlinux/$repo/os/$arch
#Server = https://glua.ua.pt/pub/archlinux/$repo/os/$arch
#Server = http://mirror.leitecastro.com/archlinux/$repo/os/$arch
#Server = https://mirror.leitecastro.com/archlinux/$repo/os/$arch
#Server = http://mirrors.up.pt/pub/archlinux/$repo/os/$arch
#Server = https://mirrors.up.pt/pub/archlinux/$repo/os/$arch
#Server = http://ftp.rnl.tecnico.ulisboa.pt/pub/archlinux/$repo/os/$arch
#Server = https://ftp.rnl.tecnico.ulisboa.pt/pub/archlinux/$repo/os/$arch

## Romania
#Server = http://mirrors.chroot.ro/archlinux/$repo/os/$arch
#Server = https://mirrors.chroot.ro/archlinux/$repo/os/$arch
#Server = http://mirror.efect.ro/archlinux/$repo/os/$arch
#Server = https://mirror.efect.ro/archlinux/$repo/os/$arch
#Server = http://ro.mirror.flokinet.net/archlinux/$repo/os/$arch
#Server = https://ro.mirror.flokinet.net/archlinux/$repo/os/$arch
#Server = http://mirrors.hosterion.ro/archlinux/$repo/os/$arch
#Server = https://mirrors.hosterion.ro/archlinux/$repo/os/$arch
#Server = http://mirrors.hostico.ro/archlinux/$repo/os/$arch
#Server = https://mirrors.hostico.ro/archlinux/$repo/os/$arch
#Server = http://archlinux.mirrors.linux.ro/$repo/os/$arch
#Server = http://mirrors.nav.ro/archlinux/$repo/os/$arch
#Server = https://ro.arch.niranjan.co/$repo/os/$arch
#Server = http://mirrors.nxthost.com/archlinux/$repo/os/$arch
#Server = https://mirrors.nxthost.com/archlinux/$repo/os/$arch
#Server = http://mirrors.pidginhost.com/arch/$repo/os/$arch
#Server = https://mirrors.pidginhost.com/arch/$repo/os/$arch

## Russia
#Server = http://archlinux.gay/archlinux/$repo/os/$arch
#Server = https://archlinux.gay/archlinux/$repo/os/$arch
#Server = http://ru.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = https://ru.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = http://mirror.kamtv.ru/archlinux/$repo/os/$arch
#Server = https://mirror.kamtv.ru/archlinux/$repo/os/$arch
#Server = http://mirror.kpfu.ru/archlinux/$repo/os/$arch
#Server = https://mirror.kpfu.ru/archlinux/$repo/os/$arch
#Server = http://mirror.murmellow.lol/archlinux/$repo/os/$arch
#Server = https://mirror.murmellow.lol/archlinux/$repo/os/$arch
#Server = http://mirror.nw-sys.ru/archlinux/$repo/os/$arch
#Server = https://mirror.nw-sys.ru/archlinux/$repo/os/$arch
#Server = http://mirrors.powernet.com.ru/archlinux/$repo/os/$arch
#Server = http://repository.su/archlinux/$repo/os/$arch
#Server = https://repository.su/archlinux/$repo/os/$arch
#Server = http://web.sketserv.ru/archlinux/$repo/os/$arch
#Server = https://web.sketserv.ru/archlinux/$repo/os/$arch
#Server = http://mirror.truenetwork.ru/archlinux/$repo/os/$arch
#Server = https://mirror.truenetwork.ru/archlinux/$repo/os/$arch
#Server = http://vladivostokst.ru/archlinux/$repo/os/$arch
#Server = https://vladivostokst.ru/archlinux/$repo/os/$arch
#Server = http://mirror.yandex.ru/archlinux/$repo/os/$arch
#Server = https://mirror.yandex.ru/archlinux/$repo/os/$arch

## RÃ©union
#Server = http://arch.mithril.re/$repo/os/$arch

## Saudi Arabia
#Server = http://sa.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = https://sa.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = http://mirror.maeen.sa/arch-mirror/$repo/os/$arch
#Server = https://mirror.maeen.sa/arch-mirror/$repo/os/$arch

## Serbia
#Server = http://mirror.pmf.kg.ac.rs/archlinux/$repo/os/$arch
#Server = http://mirror1.sox.rs/archlinux/$repo/os/$arch
#Server = https://mirror1.sox.rs/archlinux/$repo/os/$arch

## Singapore
#Server = http://mirror.aktkn.sg/archlinux/$repo/os/$arch
#Server = https://mirror.aktkn.sg/archlinux/$repo/os/$arch
#Server = http://mirror.sg.cdn-perfprod.com/archlinux/$repo/os/$arch
#Server = https://mirror.sg.cdn-perfprod.com/archlinux/$repo/os/$arch
#Server = http://sg.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = https://sg.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = https://download.nus.edu.sg/mirror/archlinux/$repo/os/$arch
#Server = http://mirror.freedif.org/archlinux/$repo/os/$arch
#Server = https://mirror.freedif.org/archlinux/$repo/os/$arch
#Server = https://singapore.mirror.pkgbuild.com/$repo/os/$arch
#Server = http://mirror.guillaumea.fr/archlinux/$repo/os/$arch
#Server = https://mirror.guillaumea.fr/archlinux/$repo/os/$arch
#Server = http://mirror.jingk.ai/archlinux/$repo/os/$arch
#Server = https://mirror.jingk.ai/archlinux/$repo/os/$arch
#Server = https://sg.arch.niranjan.co/$repo/os/$arch
#Server = http://ossmirror.mycloud.services/os/linux/archlinux/$repo/os/$arch
#Server = http://mirror.sg.gs/archlinux/$repo/os/$arch
#Server = https://mirror.sg.gs/archlinux/$repo/os/$arch

## Slovakia
#Server = http://ftp.energotel.sk/pub/linux/arch/$repo/os/$arch
#Server = https://ftp.energotel.sk/pub/linux/arch/$repo/os/$arch
#Server = http://mirror.lnx.sk/pub/linux/archlinux/$repo/os/$arch
#Server = https://mirror.lnx.sk/pub/linux/archlinux/$repo/os/$arch
#Server = http://tux.rainside.sk/archlinux/$repo/os/$arch

## Slovenia
#Server = https://mirror.archlinux.si/$repo/os/$arch
#Server = https://www.sooftware.com/mirrors/Arch-Linux/$repo/os/$arch
#Server = http://mirror.tux.si/arch/$repo/os/$arch
#Server = https://mirror.tux.si/arch/$repo/os/$arch

## South Africa
#Server = http://archlinux.za.mirror.allworldit.com/archlinux/$repo/os/$arch
#Server = https://archlinux.za.mirror.allworldit.com/archlinux/$repo/os/$arch
#Server = http://za.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = https://za.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = https://johannesburg.mirror.pkgbuild.com/$repo/os/$arch
#Server = http://mirror.is.co.za/mirror/archlinux.org/$repo/os/$arch
#Server = http://archlinux.mirror.net.za/$repo/os/$arch
#Server = https://archlinux.mirror.net.za/$repo/os/$arch
#Server = http://mirrors.urbanwave.co.za/archlinux/$repo/os/$arch
#Server = https://mirrors.urbanwave.co.za/archlinux/$repo/os/$arch

## South Korea
#Server = http://kr.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = https://kr.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = http://mirror.distly.kr/archlinux/$repo/os/$arch
#Server = https://mirror.distly.kr/archlinux/$repo/os/$arch
#Server = http://ftp.kaist.ac.kr/ArchLinux/$repo/os/$arch
#Server = http://mirror.funami.tech/arch/$repo/os/$arch
#Server = https://mirror.funami.tech/arch/$repo/os/$arch
#Server = https://mirror.hemino.net/archlinux/$repo/os/$arch
#Server = http://ftp.hrts.kr/archlinux/$repo/os/$arch
#Server = https://ftp.hrts.kr/archlinux/$repo/os/$arch
#Server = http://mirror.keiminem.com/archlinux/$repo/os/$arch
#Server = http://mirror2.keiminem.com/archlinux/$repo/os/$arch
#Server = https://mirror.keiminem.com/archlinux/$repo/os/$arch
#Server = https://mirror2.keiminem.com/archlinux/$repo/os/$arch
#Server = https://mirror.krfoss.org/archlinux/$repo/os/$arch
#Server = http://ftp.lanet.kr/pub/archlinux/$repo/os/$arch
#Server = https://ftp.lanet.kr/pub/archlinux/$repo/os/$arch
#Server = http://mirror.siwoo.org/archlinux/$repo/os/$arch
#Server = https://mirror.siwoo.org/archlinux/$repo/os/$arch
#Server = http://mirror.techlabs.co.kr/archlinux/$repo/os/$arch
#Server = https://mirror.techlabs.co.kr/archlinux/$repo/os/$arch
#Server = http://mirror.wane.kr/archlinux/$repo/os/$arch
#Server = https://mirror.wane.kr/archlinux/$repo/os/$arch
#Server = http://mirror.yuki.net.uk/archlinux/$repo/os/$arch
#Server = https://mirror.yuki.net.uk/archlinux/$repo/os/$arch

## Spain
#Server = http://mirror.es.cdn-perfprod.com/archlinux/$repo/os/$arch
#Server = https://mirror.es.cdn-perfprod.com/archlinux/$repo/os/$arch
#Server = http://es.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = https://es.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = https://nox.panibrez.com/archlinux/$repo/os/$arch
#Server = http://mirror.raiolanetworks.com/archlinux/$repo/os/$arch
#Server = https://mirror.raiolanetworks.com/archlinux/$repo/os/$arch
#Server = https://ftp.rediris.es/mirror/archlinux/$repo/os/$arch

## Sweden
#Server = http://mirror.accum.se/mirror/archlinux/$repo/os/$arch
#Server = https://mirror.accum.se/mirror/archlinux/$repo/os/$arch
#Server = https://mirror.braindrainlan.nu/archlinux/$repo/os/$arch
#Server = https://umea.mirror.pkgbuild.com/$repo/os/$arch
#Server = http://ftpmirror.infania.net/mirror/archlinux/$repo/os/$arch
#Server = http://ftp.ludd.ltu.se/mirrors/archlinux/$repo/os/$arch
#Server = https://ftp.ludd.ltu.se/mirrors/archlinux/$repo/os/$arch
#Server = http://ftp.lysator.liu.se/pub/archlinux/$repo/os/$arch
#Server = https://ftp.lysator.liu.se/pub/archlinux/$repo/os/$arch
#Server = http://mirror.bahnhof.net/pub/archlinux/$repo/os/$arch
#Server = https://mirror.bahnhof.net/pub/archlinux/$repo/os/$arch
#Server = http://ftp.myrveln.se/pub/linux/archlinux/$repo/os/$arch
#Server = https://ftp.myrveln.se/pub/linux/archlinux/$repo/os/$arch
#Server = https://mirror.osbeck.com/archlinux/$repo/os/$arch
#Server = http://mirror.retropc.se/archlinux/$repo/os/$arch
#Server = https://mirror.retropc.se/archlinux/$repo/os/$arch
#Server = http://mirror.tedwall.se/archlinux/$repo/os/$arch
#Server = https://mirror.tedwall.se/archlinux/$repo/os/$arch
#Server = https://mirror.zyner.org/mirror/archlinux/$repo/os/$arch

## Switzerland
#Server = http://pkg.adfinis-on-exoscale.ch/archlinux-pkgbuild/$repo/os/$arch
#Server = http://pkg.adfinis-on-exoscale.ch/archlinux/$repo/os/$arch
#Server = https://pkg.adfinis-on-exoscale.ch/archlinux-pkgbuild/$repo/os/$arch
#Server = https://pkg.adfinis-on-exoscale.ch/archlinux/$repo/os/$arch
#Server = http://mirror.arch-linux.ch/archlinux/$repo/os/$arch
#Server = https://mirror.arch-linux.ch/archlinux/$repo/os/$arch
#Server = https://archlinux.lan.brgn.ch/archlinux/$repo/os/$arch
#Server = http://ch.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = https://ch.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = http://mirror.hb9hil.org/archlinux/$repo/os/$arch
#Server = https://mirror.hb9hil.org/archlinux/$repo/os/$arch
#Server = http://mirror.init7.net/archlinux/$repo/os/$arch
#Server = https://mirror.init7.net/archlinux/$repo/os/$arch
#Server = http://mirror.metanet.ch/archlinux/$repo/os/$arch
#Server = https://mirror.metanet.ch/archlinux/$repo/os/$arch
#Server = http://mirror.puzzle.ch/archlinux/$repo/os/$arch
#Server = https://mirror.puzzle.ch/archlinux/$repo/os/$arch
#Server = https://ch.mirror.shiro.lol/archlinux/$repo/os/$arch
#Server = https://theswissbay.ch/archlinux/$repo/os/$arch
#Server = https://mirror.ungleich.ch/mirror/packages/archlinux/$repo/os/$arch
#Server = https://mirror.worldhotspot.org/archlinux/$repo/os/$arch

## Taiwan
#Server = http://mirror.archlinux.tw/ArchLinux/$repo/os/$arch
#Server = https://mirror.archlinux.tw/ArchLinux/$repo/os/$arch
#Server = http://archlinux.ccns.ncku.edu.tw/archlinux/$repo/os/$arch
#Server = https://archlinux.ccns.ncku.edu.tw/archlinux/$repo/os/$arch
#Server = http://tw.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = https://tw.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = http://free.nchc.org.tw/arch/$repo/os/$arch
#Server = https://taipei.mirror.pkgbuild.com/$repo/os/$arch
#Server = http://archlinux.cs.nycu.edu.tw/$repo/os/$arch
#Server = https://archlinux.cs.nycu.edu.tw/$repo/os/$arch
#Server = http://ftp.tku.edu.tw/Linux/ArchLinux/$repo/os/$arch
#Server = http://mirror.twds.com.tw/archlinux/$repo/os/$arch
#Server = https://mirror.twds.com.tw/archlinux/$repo/os/$arch

## Thailand
#Server = https://mirror.cyberbits.asia/archlinux/$repo/os/$arch
#Server = http://mirror.kku.ac.th/archlinux/$repo/os/$arch
#Server = https://mirror.kku.ac.th/archlinux/$repo/os/$arch

## TÃ¼rkiye
#Server = http://ftp.linux.org.tr/archlinux/$repo/os/$arch
#Server = https://tr.arch.niranjan.co/$repo/os/$arch
#Server = http://mirror.nucc.tr/arch/$repo/os/$arch
#Server = https://mirror.nucc.tr/arch/$repo/os/$arch
#Server = http://mirror.timtal.com.tr/archlinux/$repo/os/$arch
#Server = https://mirror.timtal.com.tr/archlinux/$repo/os/$arch

## Ukraine
#Server = http://distrohub.kyiv.ua/archlinux/$repo/os/$arch
#Server = https://distrohub.kyiv.ua/archlinux/$repo/os/$arch
#Server = http://repo.hyron.dev/archlinux/$repo/os/$arch
#Server = https://repo.hyron.dev/archlinux/$repo/os/$arch
#Server = http://mirror.hostiko.network/archlinux/$repo/os/$arch
#Server = https://mirror.hostiko.network/archlinux/$repo/os/$arch
#Server = http://archlinux.ip-connect.vn.ua/$repo/os/$arch
#Server = https://archlinux.ip-connect.vn.ua/$repo/os/$arch
#Server = http://mirror.mirohost.net/archlinux/$repo/os/$arch
#Server = https://mirror.mirohost.net/archlinux/$repo/os/$arch
#Server = http://mirrors.reitarovskyi.com.ua/archlinux/$repo/os/$arch
#Server = https://mirrors.reitarovskyi.com.ua/archlinux/$repo/os/$arch
#Server = http://mirror.trapmaid.org/archlinux/$repo/os/$arch
#Server = https://mirror.trapmaid.org/archlinux/$repo/os/$arch

## United Arab Emirates
#Server = https://mirror.hafeezh.com/archlinux/$repo/os/$arch

## United Kingdom
#Server = http://archlinux.uk.mirror.allworldit.com/archlinux/$repo/os/$arch
#Server = https://archlinux.uk.mirror.allworldit.com/archlinux/$repo/os/$arch
#Server = https://repo.c48.uk/arch/$repo/os/$arch
#Server = https://uk.repo.c48.uk/arch/$repo/os/$arch
#Server = http://gb.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = https://gb.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = https://london.mirror.pkgbuild.com/$repo/os/$arch
#Server = http://mirror.marcusn.net/archlinux/$repo/os/$arch
#Server = https://mirror.marcusn.net/archlinux/$repo/os/$arch
#Server = http://mirrors.melbourne.co.uk/archlinux/$repo/os/$arch
#Server = https://mirrors.melbourne.co.uk/archlinux/$repo/os/$arch
#Server = http://www.mirrorservice.org/sites/ftp.archlinux.org/$repo/os/$arch
#Server = https://www.mirrorservice.org/sites/ftp.archlinux.org/$repo/os/$arch
#Server = http://mirror.netweaver.uk/archlinux/$repo/os/$arch
#Server = https://mirror.netweaver.uk/archlinux/$repo/os/$arch
#Server = http://lon.mirror.rackspace.com/archlinux/$repo/os/$arch
#Server = https://lon.mirror.rackspace.com/archlinux/$repo/os/$arch
#Server = http://mirror.server.net/archlinux/$repo/os/$arch
#Server = https://mirror.server.net/archlinux/$repo/os/$arch
#Server = https://repo.slithery.uk/$repo/os/$arch
#Server = https://mirror.st2projects.com/archlinux/$repo/os/$arch
#Server = http://mirrors.ukfast.co.uk/sites/archlinux.org/$repo/os/$arch
#Server = https://mirrors.ukfast.co.uk/sites/archlinux.org/$repo/os/$arch
#Server = http://mirror.cov.ukservers.com/archlinux/$repo/os/$arch
#Server = https://mirror.cov.ukservers.com/archlinux/$repo/os/$arch

## United States
#Server = http://mirrors.acm.wpi.edu/archlinux/$repo/os/$arch
#Server = http://mirror.adectra.com/archlinux/$repo/os/$arch
#Server = https://mirror.adectra.com/archlinux/$repo/os/$arch
#Server = https://mirror.akane.network/archmirror/$repo/os/$arch
#Server = http://mirror.arizona.edu/archlinux/$repo/os/$arch
#Server = https://mirror.arizona.edu/archlinux/$repo/os/$arch
#Server = http://arlm.tyzoid.com/$repo/os/$arch
#Server = https://arlm.tyzoid.com/$repo/os/$arch
#Server = http://mirrors.bloomu.edu/archlinux/$repo/os/$arch
#Server = https://mirrors.bloomu.edu/archlinux/$repo/os/$arch
#Server = https://arch-mirror.brightlight.today/$repo/os/$arch
#Server = http://mirrors.cat.pdx.edu/archlinux/$repo/os/$arch
#Server = http://mirror.us.cdn-perfprod.com/archlinux/$repo/os/$arch
#Server = https://mirror.us.cdn-perfprod.com/archlinux/$repo/os/$arch
#Server = http://us.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = https://us.mirrors.cicku.me/archlinux/$repo/os/$arch
#Server = http://mirror.clarkson.edu/archlinux/$repo/os/$arch
#Server = https://mirror.clarkson.edu/archlinux/$repo/os/$arch
#Server = http://mirror.colonelhosting.com/archlinux/$repo/os/$arch
#Server = https://mirror.colonelhosting.com/archlinux/$repo/os/$arch
#Server = http://arch.mirror.constant.com/$repo/os/$arch
#Server = https://arch.mirror.constant.com/$repo/os/$arch
#Server = http://mirror.cs.odu.edu/archlinux/$repo/os/$arch
#Server = https://mirror.cs.odu.edu/archlinux/$repo/os/$arch
#Server = http://mirror.cs.vt.edu/pub/ArchLinux/$repo/os/$arch
#Server = http://repo.customcomputercare.com/archlinux/$repo/os/$arch
#Server = https://repo.customcomputercare.com/archlinux/$repo/os/$arch
#Server = http://distro.ibiblio.org/archlinux/$repo/os/$arch
#Server = http://mirror.ette.biz/archlinux/$repo/os/$arch
#Server = https://mirror.ette.biz/archlinux/$repo/os/$arch
#Server = http://codingflyboy.mm.fcix.net/archlinux/$repo/os/$arch
#Server = http://coresite.mm.fcix.net/archlinux/$repo/os/$arch
#Server = http://forksystems.mm.fcix.net/archlinux/$repo/os/$arch
#Server = http://irltoolkit.mm.fcix.net/archlinux/$repo/os/$arch
#Server = http://mirror.fcix.net/archlinux/$repo/os/$arch
#Server = http://mnvoip.mm.fcix.net/archlinux/$repo/os/$arch
#Server = http://nnenix.mm.fcix.net/archlinux/$repo/os/$arch
#Server = http://nocix.mm.fcix.net/archlinux/$repo/os/$arch
#Server = http://ohioix.mm.fcix.net/archlinux/$repo/os/$arch
#Server = http://opencolo.mm.fcix.net/archlinux/$repo/os/$arch
#Server = http://southfront.mm.fcix.net/archlinux/$repo/os/$arch
#Server = http://volico.mm.fcix.net/archlinux/$repo/os/$arch
#Server = http://ziply.mm.fcix.net/archlinux/$repo/os/$arch
#Server = https://codingflyboy.mm.fcix.net/archlinux/$repo/os/$arch
#Server = https://coresite.mm.fcix.net/archlinux/$repo/os/$arch
#Server = https://forksystems.mm.fcix.net/archlinux/$repo/os/$arch
#Server = https://irltoolkit.mm.fcix.net/archlinux/$repo/os/$arch
#Server = https://mirror.fcix.net/archlinux/$repo/os/$arch
#Server = https://mnvoip.mm.fcix.net/archlinux/$repo/os/$arch
#Server = https://nnenix.mm.fcix.net/archlinux/$repo/os/$arch
#Server = https://nocix.mm.fcix.net/archlinux/$repo/os/$arch
#Server = https://ohioix.mm.fcix.net/archlinux/$repo/os/$arch
#Server = https://opencolo.mm.fcix.net/archlinux/$repo/os/$arch
#Server = https://southfront.mm.fcix.net/archlinux/$repo/os/$arch
#Server = https://volico.mm.fcix.net/archlinux/$repo/os/$arch
#Server = https://ziply.mm.fcix.net/archlinux/$repo/os/$arch
#Server = http://mirror.fossable.org/archlinux/$repo/os/$arch
#Server = https://losangeles.mirror.pkgbuild.com/$repo/os/$arch
#Server = http://mirrors.gigenet.com/archlinux/$repo/os/$arch
#Server = https://mirror.givebytes.net/archlinux/$repo/os/$arch
#Server = https://arch.goober.cloud/$repo/os/$arch
#Server = http://mirror.hasphetica.win/archlinux/$repo/os/$arch
#Server = https://mirror.hasphetica.win/archlinux/$repo/os/$arch
#Server = http://arch.hu.fo/archlinux/$repo/os/$arch
#Server = https://arch.hu.fo/archlinux/$repo/os/$arch
#Server = http://arch.hugeblank.dev/$repo/os/$arch
#Server = https://arch.hugeblank.dev/$repo/os/$arch
#Server = http://repo.ialab.dsu.edu/archlinux/$repo/os/$arch
#Server = https://repo.ialab.dsu.edu/archlinux/$repo/os/$arch
#Server = http://mirrors.iu13.net/archlinux/$repo/os/$arch
#Server = https://mirrors.iu13.net/archlinux/$repo/os/$arch
#Server = https://arch.mirror.k0.ae/$repo/os/$arch
#Server = http://mirrors.kernel.org/archlinux/$repo/os/$arch
#Server = https://mirrors.kernel.org/archlinux/$repo/os/$arch
#Server = https://mirrors.lahansons.com/archlinux/$repo/os/$arch
#Server = http://mirror.sfo12.us.leaseweb.net/archlinux/$repo/os/$arch
#Server = http://mirror.wdc1.us.leaseweb.net/archlinux/$repo/os/$arch
#Server = https://mirror.sfo12.us.leaseweb.net/archlinux/$repo/os/$arch
#Server = https://mirror.wdc1.us.leaseweb.net/archlinux/$repo/os/$arch
#Server = http://mirrors.liquidweb.com/archlinux/$repo/os/$arch
#Server = https://mirrors.logal.dev/archlinux/$repo/os/$arch
#Server = http://mirrors.lug.mtu.edu/archlinux/$repo/os/$arch
#Server = https://mirrors.lug.mtu.edu/archlinux/$repo/os/$arch
#Server = https://m.lqy.me/arch/$repo/os/$arch
#Server = https://arch.mirror.marcusspencer.us:4443/archlinux/$repo/os/$arch
#Server = http://mirror.math.princeton.edu/pub/archlinux/$repo/os/$arch
#Server = http://mirror.metrocast.net/archlinux/$repo/os/$arch
#Server = http://arch.miningtcup.me/$repo/os/$arch
#Server = https://arch.miningtcup.me/$repo/os/$arch
#Server = https://mirrors.shr.cx/arch/$repo/os/$arch
#Server = http://iad.mirrors.misaka.one/archlinux/$repo/os/$arch
#Server = https://iad.mirrors.misaka.one/archlinux/$repo/os/$arch
#Server = http://repo.miserver.it.umich.edu/archlinux/$repo/os/$arch
#Server = http://mirrors.mit.edu/archlinux/$repo/os/$arch
#Server = https://mirrors.mit.edu/archlinux/$repo/os/$arch
#Server = http://mirror.mra.sh/archlinux/$repo/os/$arch
#Server = https://mirror.mra.sh/archlinux/$repo/os/$arch
#Server = https://us.arch.niranjan.co/$repo/os/$arch
#Server = http://mirrors.ocf.berkeley.edu/archlinux/$repo/os/$arch
#Server = https://mirrors.ocf.berkeley.edu/archlinux/$repo/os/$arch
#Server = http://archmirror1.octyl.net/$repo/os/$arch
#Server = https://archmirror1.octyl.net/$repo/os/$arch
#Server = http://ftp.osuosl.org/pub/archlinux/$repo/os/$arch
#Server = https://ftp.osuosl.org/pub/archlinux/$repo/os/$arch
#Server = https://mirror.pilotfiber.com/archlinux/$repo/os/$arch
#Server = http://dfw.mirror.rackspace.com/archlinux/$repo/os/$arch
#Server = http://iad.mirror.rackspace.com/archlinux/$repo/os/$arch
#Server = http://ord.mirror.rackspace.com/archlinux/$repo/os/$arch
#Server = https://dfw.mirror.rackspace.com/archlinux/$repo/os/$arch
#Server = https://iad.mirror.rackspace.com/archlinux/$repo/os/$arch
#Server = https://ord.mirror.rackspace.com/archlinux/$repo/os/$arch
#Server = http://plug-mirror.rcac.purdue.edu/archlinux/$repo/os/$arch
#Server = https://plug-mirror.rcac.purdue.edu/archlinux/$repo/os/$arch
#Server = http://mirrors.rit.edu/archlinux/$repo/os/$arch
#Server = https://mirrors.rit.edu/archlinux/$repo/os/$arch
#Server = http://mirror.siena.edu/archlinux/$repo/os/$arch
#Server = http://mirrors.smeal.xyz/arch-linux/$repo/os/$arch
#Server = https://mirrors.smeal.xyz/arch-linux/$repo/os/$arch
#Server = http://mirrors.sonic.net/archlinux/$repo/os/$arch
#Server = https://mirrors.sonic.net/archlinux/$repo/os/$arch
#Server = https://us-mnz.soulharsh007.dev/archlinux/$repo/os/$arch
#Server = http://mirror.pit.teraswitch.com/archlinux/$repo/os/$arch
#Server = https://mirror.pit.teraswitch.com/archlinux/$repo/os/$arch
#Server = https://mirror.theash.xyz/arch/$repo/os/$arch
#Server = http://mirror.umd.edu/archlinux/$repo/os/$arch
#Server = https://mirror.umd.edu/archlinux/$repo/os/$arch
#Server = http://mirrors.vectair.net/archlinux/$repo/os/$arch
#Server = https://mirrors.vectair.net/archlinux/$repo/os/$arch
#Server = http://mirror.vtti.vt.edu/archlinux/$repo/os/$arch
#Server = http://wcbmedia.io:8000/$repo/os/$arch
#Server = http://mirrors.xmission.com/archlinux/$repo/os/$arch
#Server = http://mirrors.xtom.com/archlinux/$repo/os/$arch
#Server = https://mirrors.xtom.com/archlinux/$repo/os/$arch
#Server = https://yonderly.org/mirrors/archlinux/$repo/os/$arch
#Server = https://mirror.zackmyers.io/archlinux/$repo/os/$arch
#Server = https://zxcvfdsa.com/arch/$repo/os/$arch

## Uzbekistan
#Server = http://mirror.dc.uz/arch/$repo/os/$arch
#Server = https://mirror.dc.uz/arch/$repo/os/$arch

## Vietnam
#Server = http://mirror.bizflycloud.vn/archlinux/$repo/os/$arch
#Server = https://mirrors.huongnguyen.dev/arch/$repo/os/$arch
#Server = https://mirror.meowsmp.net/arch/$repo/os/$arch
#Server = https://mirrors.nguyenhoang.cloud/archlinux/$repo/os/$arch
RAW_MIRROR_LIST

# --- HELPER: Extract fallback date using bash regex ---
get_fallback_date() {
    local date_str="Unknown Date"
    if [[ "$FALLBACK_RAW_DATA" =~ Generated\ on\ ([0-9]{4}-[0-9]{2}-[0-9]{2}) ]]; then
        date_str="${BASH_REMATCH[1]}"
    fi
    printf '%s' "$date_str"
}

# --- MAIN LOGIC ---
update_mirrors() {
    local country input_country fallback_date choice

    while true; do
        printf '\n%s:: Mirrorlist Configuration%s\n' "$B" "$NC"
        printf '   --------------------------------------------------------\n'
        printf '   %sNOTE TO GLOBAL USERS:%s\n' "$Y" "$NC"
        printf '   Type %slist%s to view all available countries.\n' "$B" "$NC"
        printf '   Press %s[Enter]%s to use the default (%s).\n' "$B" "$NC" "$DEFAULT_COUNTRY"
        printf '   --------------------------------------------------------\n'

        read -r -p ":: Enter country: " input_country

        # Handle explicit list request
        if [[ "${input_country,,}" == "list" ]]; then
            log_warn "Retrieving country list..."
            reflector --list-countries
            printf '\n'
            continue
        fi

        # Apply default if input is empty
        country="${input_country:-$DEFAULT_COUNTRY}"

        # If resolved country is 'list', show list and loop
        if [[ "${country,,}" == "list" ]]; then
            log_warn "Retrieving country list..."
            reflector --list-countries
            printf '\n'
            continue
        fi

        log_warn "Running Reflector for region: ${country}..."

        # Attempt reflector update
        if reflector --country "$country" --latest 10 --protocol https \
                     --sort rate --download-timeout 5 --save "$TARGET_FILE"; then
            log_ok "Reflector success! Mirrors updated."
            log_info "Syncing package database..."
            # -Syy is safer for full mirror replacement
            pacman -Syy
            break
        fi

        # Reflector failed — present recovery menu
        fallback_date="$(get_fallback_date)"

        printf '\n%s!! Reflector failed to update mirrors for "%s".%s\n' "$R" "$country" "$NC"
        printf '   1) Retry (Enter new country)\n'
        printf '   2) Use Global Fallback Mirrors (%s)\n' "$fallback_date"
        printf '   3) Do nothing (Abort changes)\n'

        read -r -p ":: Select an option [1-3]: " choice

        case "$choice" in
            1)
                log_info "Retrying..."
                ;;
            2)
                local fb_country fb_input
                while true; do
                    printf '\n'
                    read -r -p ":: Enter country for fallback list (type 'list' to view): " fb_input
                    fb_country="${fb_input:-Worldwide}" # Default to Worldwide if empty

                    if [[ "${fb_input,,}" == "list" ]]; then
                        log_info "Available Fallback Countries:"
                        # List all lines starting with '## ' excluding headers
                        grep '^## ' <<< "$FALLBACK_RAW_DATA" | sed 's/^## //g' | grep -vE "^Arch Linux|^Generated on|^$" | column
                        continue
                    fi

                    # Check if country exists in RAW DATA
                    # We search for "## CountryName"
                    if grep -q "^## ${fb_country}$" <<< "$FALLBACK_RAW_DATA"; then
                         log_warn "Applying fallback mirrors for: $fb_country"
                         
                         # Use awk to extract the block between "## Country" and the next "##"
                         # and uncomment #Server lines only within that block
                         awk -v country="## $fb_country" '
                            $0 == country { found=1; next }     # Found start block
                            /^## / { if (found) exit }          # Found next block header, exit
                            found { 
                                sub(/^#Server/, "Server");      # Uncomment server lines
                                print                           # Print line
                            }
                         ' <<< "$FALLBACK_RAW_DATA" > "$TARGET_FILE"

                         if [[ -s "$TARGET_FILE" ]]; then
                             log_ok "Fallback mirrors applied for $fb_country."
                             log_info "Syncing package database..."
                             pacman -Syy
                             break 2 # Break out of both loops
                         else
                             log_err "Failed to extract mirrors. Please try another country."
                         fi
                    else
                        log_err "Country '$fb_country' not found in fallback data."
                    fi
                done
                ;;
            3)
                log_warn "Skipping mirror update."
                break
                ;;
            *)
                log_err "Invalid selection."
                ;;
        esac
    done
}

# --- ENTRY POINT ---
update_mirrors

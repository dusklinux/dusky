#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <stdint.h>
#include <stdarg.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>
#include <stdlib.h>
#include <stdatomic.h>

/*
 * runner_shim: opt-in game-specific I/O workarounds.
 * 
 * 1. Mono / .NET File Sharing Violation Fix:
 *    Many Linux native games built on Mono (FNA, XNA, MonoGame, Unity) call
 *    new FileStream(path, FileMode.Open), which defaults to FileAccess.ReadWrite
 *    and FileShare.Read. When multiple threads stream assets simultaneously,
 *    Mono's internal file_share_table keyed by (dev, ino) raises a false
 *    sharing violation (ERROR_SHARING_VIOLATION 32), crashing the game.
 *    With MASTER_RUNNER_SHIM_MONO_INODES set, a descriptor-specific inode
 *    stream handle is isolated and concurrent reading never conflicts.
 *
 * 2. DwarFS / fuse-overlayfs Copy-Up Protection:
 *    When games run on compressed DwarFS with fuse-overlayfs, opening static
 *    read-only archives with write intent (e.g. .NET's default O_RDWR) tricks
 *    the overlay into copying up entire multi-gigabyte files to disk.
 *    With MASTER_RUNNER_SHIM_READONLY_ASSETS set, this demotes O_RDWR to O_RDONLY for static asset
 *    archives, saving gigabytes of disk writes, avoiding I/O stalls, and
 *    preventing file duplication.
 */

static int is_game_process(void) {
    extern char *program_invocation_short_name;
    if (!program_invocation_short_name) return 1;
    const char *name = program_invocation_short_name;
    if (strcmp(name, "bash") == 0 || strcmp(name, "sh") == 0 ||
        strcmp(name, "cp") == 0 || strcmp(name, "mv") == 0 ||
        strcmp(name, "rm") == 0 || strcmp(name, "mkdir") == 0 ||
        strcmp(name, "systemd-run") == 0 || strcmp(name, "python3") == 0 ||
        strcmp(name, "bwrap") == 0 || strcmp(name, "tar") == 0) {
        return 0;
    }
    return 1;
}

static int is_mono_runtime(void) {
    /* This workaround changes inode identity and must be explicitly selected. */
    if (!getenv("MASTER_RUNNER_SHIM_MONO_INODES") || !is_game_process()) return 0;
    if (dlsym(RTLD_DEFAULT, "mono_init") != NULL ||
        dlsym(RTLD_DEFAULT, "mono_w32file_create") != NULL ||
        dlsym(RTLD_DEFAULT, "mono_runtime_init") != NULL ||
        getenv("MONO_PATH") != NULL) {
        return 1;
    }
    return 0;
}

static ino_t descriptor_inode(const struct stat *buf, int fd) {
    /* Keep repeated queries stable for one descriptor while isolating opens. */
    uint64_t x = (uint64_t)buf->st_ino ^ ((uint64_t)buf->st_dev << 32);
    x ^= (uint64_t)(unsigned int)fd * 0x9e3779b97f4a7c15ULL;
    x ^= x >> 30;
    x *= 0xbf58476d1ce4e5b9ULL;
    x ^= x >> 27;
    x *= 0x94d049bb133111ebULL;
    return (ino_t)(x ^ (x >> 31));
}

/* Hook __fxstat: Mono specifically calls __fxstat(1, fd, buf) in mono_w32file_create */
typedef int (*fxstat_fn)(int ver, int fd, struct stat *buf);
static _Atomic(fxstat_fn) real_fxstat = NULL;

int __fxstat(int ver, int fd, struct stat *buf) {
    fxstat_fn fn = atomic_load(&real_fxstat);
    if (!fn) {
        fn = (fxstat_fn)dlsym(RTLD_NEXT, "__fxstat");
        if (!fn) { errno = ENOSYS; return -1; }
        atomic_store(&real_fxstat, fn);
    }
    int res = fn(ver, fd, buf);
    if (res == 0 && buf && is_mono_runtime()) {
        buf->st_ino = descriptor_inode(buf, fd);
    }
    return res;
}

/* Also hook fstat */
typedef int (*fstat_fn)(int fd, struct stat *buf);
static _Atomic(fstat_fn) real_fstat = NULL;

int fstat(int fd, struct stat *buf) {
    fstat_fn fn = atomic_load(&real_fstat);
    if (!fn) {
        fn = (fstat_fn)dlsym(RTLD_NEXT, "fstat");
        if (!fn) { errno = ENOSYS; return -1; }
        atomic_store(&real_fstat, fn);
    }
    int res = fn(fd, buf);
    if (res == 0 && is_mono_runtime()) {
        buf->st_ino = descriptor_inode(buf, fd);
    }
    return res;
}

/* Check if a file is an immutable game asset that should never trigger copy-up */
static int is_static_asset(const char *path) {
    if (!path) return 0;
    const char *ext = strrchr(path, '.');
    if (ext) {
        if (strcasecmp(ext, ".wem") == 0 || strcasecmp(ext, ".bnk") == 0 ||
            strcasecmp(ext, ".ogv") == 0 || strcasecmp(ext, ".xnb") == 0 ||
            strcasecmp(ext, ".pak") == 0 || strcasecmp(ext, ".vpk") == 0 ||
            strcasecmp(ext, ".pck") == 0 || strcasecmp(ext, ".bundle") == 0 ||
            strcasecmp(ext, ".assets") == 0 || strcasecmp(ext, ".bank") == 0 ||
            strcasecmp(ext, ".mp4") == 0 || strcasecmp(ext, ".mkv") == 0 ||
            strcasecmp(ext, ".bk2") == 0) {
            return 1;
        }
    }
    return 0;
}

static int needs_open_mode(int flags) {
    return (flags & O_CREAT) || ((flags & O_TMPFILE) == O_TMPFILE);
}

static int readonly_asset_flags(const char *pathname, int flags) {
    if (getenv("MASTER_RUNNER_SHIM_READONLY_ASSETS") && is_game_process() &&
        (flags & O_ACCMODE) == O_RDWR &&
        !(flags & (O_CREAT | O_TRUNC | O_APPEND)) &&
        (flags & O_TMPFILE) != O_TMPFILE && is_static_asset(pathname)) {
        return (flags & ~O_ACCMODE) | O_RDONLY;
    }
    return flags;
}

/* Hook open to prevent DwarFS / fuse-overlayfs copy-up of multi-gigabyte read-only assets */
typedef int (*open_fn)(const char *pathname, int flags, ...);
static _Atomic(open_fn) real_open = NULL;

int open(const char *pathname, int flags, ...) {
    mode_t mode = 0;
    if (needs_open_mode(flags)) {
        va_list args;
        va_start(args, flags);
        mode = va_arg(args, mode_t);
        va_end(args);
    }
    open_fn fn = atomic_load(&real_open);
    if (!fn) {
        fn = (open_fn)dlsym(RTLD_NEXT, "open");
        if (!fn) { errno = ENOSYS; return -1; }
        atomic_store(&real_open, fn);
    }
    return fn(pathname, readonly_asset_flags(pathname, flags), mode);
}

typedef int (*open64_fn)(const char *pathname, int flags, ...);
static _Atomic(open64_fn) real_open64 = NULL;

int open64(const char *pathname, int flags, ...) {
    mode_t mode = 0;
    if (needs_open_mode(flags)) {
        va_list args;
        va_start(args, flags);
        mode = va_arg(args, mode_t);
        va_end(args);
    }
    open64_fn fn = atomic_load(&real_open64);
    if (!fn) {
        fn = (open64_fn)dlsym(RTLD_NEXT, "open64");
        if (!fn) { errno = ENOSYS; return -1; }
        atomic_store(&real_open64, fn);
    }
    return fn(pathname, readonly_asset_flags(pathname, flags), mode);
}

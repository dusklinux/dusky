"""Atomic no-clobber publication on Linux, including FAT/exFAT destinations."""

import ctypes
import os
from pathlib import Path

_libc = ctypes.CDLL(None, use_errno=True)
_renameat2 = _libc.renameat2
_renameat2.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                      ctypes.c_char_p, ctypes.c_uint)
_renameat2.restype = ctypes.c_int
_AT_FDCWD = -100
_RENAME_NOREPLACE = 1


def rename_no_replace(source: Path, target: Path):
    if _renameat2(_AT_FDCWD, os.fsencode(source), _AT_FDCWD,
                  os.fsencode(target), _RENAME_NOREPLACE) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(target))

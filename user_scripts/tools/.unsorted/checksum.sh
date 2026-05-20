#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 <filename>"; exit 1; }
[[ ! -f "$1" ]] && { echo "Error: File '$1' not found."; exit 1; }
echo "MD5:    $(md5sum "$1" | cut -d' ' -f1)"
echo "SHA1:   $(sha1sum "$1" | cut -d' ' -f1)"
echo "SHA256: $(sha256sum "$1" | cut -d' ' -f1)"
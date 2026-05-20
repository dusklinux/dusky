#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 '<password>'"; exit 1; }
python3 -c "import hashlib; print(hashlib.scrypt('$1'.encode(), salt=b'salt', n=16384, r=8, p=1).hex())"
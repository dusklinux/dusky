#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 '<password>'"; exit 1; }
python3 -c "import hashlib; print(hashlib.pbkdf2_hmac('sha256', '$1'.encode(), b'salt', 100000).hex())"
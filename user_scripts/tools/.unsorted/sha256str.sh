#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 '<text>'"; exit 1; }
python3 -c "import hashlib; print(hashlib.sha256(b'$1').hexdigest())"
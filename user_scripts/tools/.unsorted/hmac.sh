#!/bin/bash
[[ $# -ne 2 ]] && { echo "Usage: $0 <key> <message>"; exit 1; }
python3 -c "import hmac,hashlib,sys; print(hmac.new(b'$1',b'$2',hashlib.sha256).hexdigest())"
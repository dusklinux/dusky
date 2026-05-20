#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 '<password>'"; exit 1; }
python3 -c "import bcrypt; print(bcrypt.hashpw('$1'.encode(), bcrypt.gensalt()).decode())"
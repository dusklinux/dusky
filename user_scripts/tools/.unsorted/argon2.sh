#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 '<password>'"; exit 1; }
python3 -c "import argon2; print(argon2.PasswordHasher().hash('$1'))"
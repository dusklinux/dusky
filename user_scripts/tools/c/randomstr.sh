#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 <length>"; exit 1; }
python3 -c "import secrets; print(secrets.token_urlsafe(${1:-32}))"
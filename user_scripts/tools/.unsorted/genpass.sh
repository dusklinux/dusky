#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 <length>"; exit 1; }
openssl rand -base64 "${1:-32}" | tr -d '\n=/' | cut -c1-"$1"
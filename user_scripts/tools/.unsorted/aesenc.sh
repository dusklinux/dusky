#!/bin/bash
[[ $# -ne 3 ]] && { echo "Usage: $0 <filename> <key> <output>"; exit 1; }
openssl enc -aes-128-cbc -salt -in "$1" -out "$3" -pass pass:"$2" -e 2>/dev/null || echo "OpenSSL required"
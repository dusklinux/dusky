#!/bin/bash
[[ $# -ne 3 ]] && { echo "Usage: $0 <file> <privkey.pem> <outfile>"; exit 1; }
openssl rsautl -decrypt -inkey "$2" -in "$1" -out "$3" 2>/dev/null || echo "OpenSSL required"
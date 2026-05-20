#!/bin/bash
[[ $# -ne 3 ]] && { echo "Usage: $0 <message> <pubkey.pem> <outfile>"; exit 1; }
openssl rsautl -encrypt -pubin -inkey "$2" -in <(echo "$1") -out "$3" 2>/dev/null || echo "OpenSSL required"
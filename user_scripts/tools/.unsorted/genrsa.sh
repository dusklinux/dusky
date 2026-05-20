#!/bin/bash
[[ $# -ne 2 ]] && { echo "Usage: $0 <bits> <output_prefix>"; exit 1; }
openssl genrsa -out "$2".pem "${1:-2048}" 2>/dev/null
openssl rsa -in "$2".pem -pubout -out "$2".pub.pem 2>/dev/null
echo "Keys: $2.pem (private), $2.pub.pem (public)"
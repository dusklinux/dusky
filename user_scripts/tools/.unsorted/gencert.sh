#!/bin/bash
[[ $# -ne 3 ]] && { echo "Usage: $0 <host> <days> <outfile.crt>"; exit 1; }
openssl req -x509 -newkey rsa:2048 -keyout "$3".key -out "$3" -days "$2" -nodes -subj "/CN=$1" 2>/dev/null
echo "Cert: $3, Key: $3.key"
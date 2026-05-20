#!/bin/bash
[[ $# -ne 3 ]] && { echo "Usage: $0 <filename> <password> <output>"; exit 1; }
openssl enc -aes-256-cbc -d -pbkdf2 -iter 100000 -in "$1" -out "$3" -pass pass:"$2"
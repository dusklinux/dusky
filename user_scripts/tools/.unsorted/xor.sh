#!/bin/bash
[[ $# -ne 2 ]] && { echo "Usage: $0 <file1> <file2>"; exit 1; }
paste <(xxd -p "$1") <(xxd -p "$2") | awk '{for(i=1;i<=NF;i+=2)printf "%x ", xor("0x"$i,"0x"$(i+1)); print ""}'
#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 <filename>"; exit 1; }
[[ ! -f "$1" ]] && { echo "Error: File '$1' not found."; exit 1; }
awk '{for(i=length($0);i>=1;i--)x=x substr($0,i,1);print x;x=""}' "$1"
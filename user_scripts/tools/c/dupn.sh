#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 <filename>"; exit 1; }
[[ ! -f "$1" ]] && { echo "Error: File '$1' not found."; exit 1; }
awk 'BEGIN{for(i=1;i<=100;i++)printf "%s%s", $0, (i%10?"":ORS)}' "$1"
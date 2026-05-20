#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 <filename>"; exit 1; }
[[ ! -f "$1" ]] && { echo "Error: File '$1' not found."; exit 1; }
awk '{for(i=1;i<=NF;i++)freq[$i]++} END{for(w in freq)print freq[w],w}' "$1" | sort -rn | head -10
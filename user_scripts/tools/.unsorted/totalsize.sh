#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 <filename>"; exit 1; }
[[ ! -f "$1" ]] && { echo "Error: File '$1' not found."; exit 1; }
find "$1" -type f -printf '%s\n' | awk '{sum+=$1} END{printf "%.0f bytes\n", sum}'
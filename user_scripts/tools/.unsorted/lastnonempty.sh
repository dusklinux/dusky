#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 <filename>"; exit 1; }
[[ ! -f "$1" ]] && { echo "Error: File '$1' not found."; exit 1; }
grep -n '[[:alnum:]]' "$1" | sed 's/:.*//' | sort -n | tail -1
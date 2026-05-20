#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 <filename>"; exit 1; }
[[ ! -f "$1" ]] && { echo "Error: File '$1' not found."; exit 1; }
python3 -m json.tool "$1" 2>/dev/null || jq . "$1" 2>/dev/null || echo "Error: json tool or jq required"
#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 '<text>'"; exit 1; }
echo "$1" | sed 's/./&\n/g' | sort | uniq -c | sort -rn
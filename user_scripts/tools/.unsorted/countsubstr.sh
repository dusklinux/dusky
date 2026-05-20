#!/bin/bash
[[ $# -ne 2 ]] && { echo "Usage: $0 '<text>' '<search>'"; exit 1; }
count=$(echo "$1" | grep -o "$2" | wc -l)
echo "Found: $count"
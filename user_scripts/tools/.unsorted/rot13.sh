#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 '<text>'"; exit 1; }
echo "$1" | tr 'A-Za-z' 'N-ZA-Mn-za-m'
#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 '<text>'"; exit 1; }
echo "$1" | sed -e 's/\([A-Z]\)/ \1/g' -e 's/^ //'
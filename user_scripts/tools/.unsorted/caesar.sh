#!/bin/bash
[[ $# -ne 2 ]] && { echo "Usage: $0 '<text>' <shift>"; exit 1; }
echo "$1" | tr 'A-Z' "$(echo {A..Z} | tr ' ' '\n' | sed -n "$2,\$p;1,$(( $2 - 1 ))p" | tr '\n' ' ' | sed 's/ //g')" | tr 'a-z' "$(echo {a..z} | tr ' ' '\n' | sed -n "$2,\$p;1,$(( $2 - 1 ))p" | tr '\n' ' ' | sed 's/ //g')"
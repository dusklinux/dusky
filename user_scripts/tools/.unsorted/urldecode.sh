#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 <filename>"; exit 1; }
[[ ! -f "$1" ]] && { echo "Error: File '$1' not found."; exit 1; }
python3 -c "import urllib.parse,sys; print(urllib.parse.unquote(open('$1').read()))" < "$1"
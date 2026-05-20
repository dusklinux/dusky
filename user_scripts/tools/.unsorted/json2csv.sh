#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 <filename>"; exit 1; }
[[ ! -f "$1" ]] && { echo "Error: File '$1' not found."; exit 1; }
python3 -c "import csv,sys,json; r=csv.DictReader(sys.stdin); print(json.dumps(list(r)))" < "$1"
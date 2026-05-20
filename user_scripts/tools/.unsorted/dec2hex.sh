#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 <num>"; exit 1; }
python3 -c "print(hex(int('$1',10))[2:])"
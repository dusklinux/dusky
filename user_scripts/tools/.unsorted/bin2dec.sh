#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 <num>"; exit 1; }
python3 -c "print(int('$1',2))"
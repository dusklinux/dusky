#!/bin/bash
[[ $# -ne 2 ]] && { echo "Usage: $0 '<string>' <count>"; exit 1; }
python3 -c "print('$1' * int('$2'))"
#!/bin/bash
[[ $# -ne 2 ]] && { echo "Usage: $0 <min> <max>"; exit 1; }
python3 -c "import random,sys; print(random.randint(int('$1'),int('$2')))"
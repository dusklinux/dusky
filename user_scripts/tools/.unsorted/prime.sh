#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 <num>"; exit 1; }
python3 -c "import math; print('Prime' if all($1%i for i in range(2,int(math.sqrt(abs($1)))+1))) and $1>1 else 'Not prime')"
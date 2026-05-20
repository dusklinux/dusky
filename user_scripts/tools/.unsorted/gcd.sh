#!/bin/bash
[[ $# -ne 2 ]] && { echo "Usage: $0 <num1> <num2>"; exit 1; }
python3 -c "print('GCD:',eval('import math;math.gcd($1,$2)'))"
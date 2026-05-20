#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 <num>"; exit 1; }
python3 -c "print([i for i in range(2,int('$1')+1) if all(i%j for j in range(2,i))])"
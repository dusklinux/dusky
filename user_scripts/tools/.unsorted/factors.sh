#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 <num>"; exit 1; }
python3 -c "
import sys
n=int('$1')
factors=[]
d=2
while d*d<=n:
    while n%d==0:
        factors.append(d)
        n//=d
    d+=1
if n>1: factors.append(n)
print(' x '.join(map(str,factors)))
"
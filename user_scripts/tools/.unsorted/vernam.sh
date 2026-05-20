#!/bin/bash
[[ $# -ne 2 ]] && { echo "Usage: $0 <filename> <key>"; exit 1; }
python3 -c "
f=open('$1','rb').read()
k=int('$2',16)
result=bytearray()
for i,b in enumerate(f):
    result.append(b^(k>>(i*8)&0xff))
open('$1.xor','wb').write(result)
print('Done: $1.xor')
"
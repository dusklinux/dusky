#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 <filename>"; exit 1; }
python3 -c "
import sys, math, collections
text = open('$1').read()
freq = collections.Counter(text)
total = len(text)
entropy = -sum((f/total)*math.log2(f/total) for f in freq.values())
print(f'Entropy: {entropy:.4f} bits/char')
print(f'Total bytes: {total}')
print(f'Unique chars: {len(freq)}')
"
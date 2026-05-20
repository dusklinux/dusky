#!/bin/bash
[[ $# -ne 2 ]] && { echo "Usage: $0 <file1> <file2>"; exit 1; }
cmp -s "$1" "$2" && echo "IDENTICAL" || echo "DIFFERENT"
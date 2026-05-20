#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 <filename>"; exit 1; }
[[ ! -f "$1" ]] && { echo "Error: File '$1' not found."; exit 1; }
sort -t. -k1,1n -k2,2n -k3,3n -k4,4n "$1"
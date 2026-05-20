#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 <filename>"; exit 1; }
[[ ! -f "$1" ]] && { echo "Error: File '$1' not found."; exit 1; }
awk -F, 'BEGIN{print "["} NR>1{printf "%s{\"col1\":\"%s\",\"col2\":\"%s\"}",(NR>2?",":""),$1,$2} END{print "]"}' "$1"
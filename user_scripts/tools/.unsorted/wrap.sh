#!/bin/bash
[[ $# -ne 2 ]] && { echo "Usage: $0 '<text>' <num>"; exit 1; }
echo "$1" | fold -w "${2:-80}"
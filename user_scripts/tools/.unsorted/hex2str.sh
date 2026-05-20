#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 '<hex>'"; exit 1; }
echo "$1" | xxd -r -p
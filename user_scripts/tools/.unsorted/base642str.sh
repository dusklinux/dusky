#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 '<base64>'"; exit 1; }
echo "$1" | base64 -d
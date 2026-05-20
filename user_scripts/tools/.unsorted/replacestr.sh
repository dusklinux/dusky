#!/bin/bash
[[ $# -ne 2 ]] && { echo "Usage: $0 '<text>' '<search>'"; exit 1; }
echo "${1//$2/}"
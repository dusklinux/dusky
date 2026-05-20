#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 <bytes>"; exit 1; }
openssl rand -hex "${1:-32}"
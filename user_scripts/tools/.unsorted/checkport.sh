#!/bin/bash
[[ $# -ne 2 ]] && { echo "Usage: $0 <host> <port>"; exit 1; }
timeout 5 bash -c "echo '' > /dev/tcp/$1/$2" 2>/dev/null && echo "OPEN" || echo "CLOSED"
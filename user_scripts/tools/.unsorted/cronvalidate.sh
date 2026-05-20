#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 '<cron expression>'"; exit 1; }
python3 -c "from croniter import croniter; croniter('$1',0)" 2>/dev/null && echo "Valid" || echo "Invalid"
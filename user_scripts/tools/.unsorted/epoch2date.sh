#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 '<seconds>'"; exit 1; }
python3 -c "import datetime; print(datetime.timedelta(seconds=${1}))"
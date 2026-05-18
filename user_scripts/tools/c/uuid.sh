#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 [-n <count>]"; exit 1; }
python3 -c "import uuid; print(uuid.uuid4())"
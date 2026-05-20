#!/bin/bash
[[ $# -ne 1 ]] && { echo "Usage: $0 '<json>'"; exit 1; }
python3 -c "import json,sys; d=json.loads('$1'); [print(k,'=',v) for k,v in d.items()]"
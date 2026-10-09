"""python -m dusky_keylogger"""

import sys

if __package__ in {None, ""}:
    from dusky_keylogger.cli import main
else:
    from .dusky_keylogger.cli import main

if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""INI state including commented defaults; active values always take priority."""
from python.engines.ini import IniConfigEngine


class BridgedIniEngine(IniConfigEngine):
    _include_comments = True

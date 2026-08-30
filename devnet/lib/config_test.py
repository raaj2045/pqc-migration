"""Tests for config value expansion. Run: python3 devnet/lib/config_test.py

Mirrors config.test.js so the two layers cannot drift.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import ROOT, expand  # noqa: E402

HOME = os.path.expanduser("~")
passed = 0


def check(name, actual, expected):
    global passed
    assert actual == expected, f"{name}: got {actual!r}, want {expected!r}"
    passed += 1


# $DEVNET_ROOT resolves to this directory, so entries pointing at scripts in
# this repository work regardless of where it was cloned.
check("$DEVNET_ROOT", expand("python3 $DEVNET_ROOT/cosmos/sendtx.py"),
      f"python3 {ROOT}/cosmos/sendtx.py")
check("$DEVNET_ROOT twice", expand("$DEVNET_ROOT/a $DEVNET_ROOT/b"),
      f"{ROOT}/a {ROOT}/b")

# $HOME and ~ both resolve to the user's home directory.
check("$HOME", expand("$HOME/devnet-workdir"), f"{HOME}/devnet-workdir")
check("~", expand("~/devnet-workdir"), f"{HOME}/devnet-workdir")
check("~ after a command", expand("python3 ~/x.py"), f"python3 {HOME}/x.py")

# Literal values pass through untouched.
check("placeholder", expand("/path/to/pqchaind"), "/path/to/pqchaind")
check("url", expand("tcp://127.0.0.1:26657"), "tcp://127.0.0.1:26657")
check("bare word", expand("validator"), "validator")
check("hex", expand("0x1260944489272988d9df"), "0x1260944489272988d9df")
check("empty", expand(""), "")

# Only exact tokens expand.
check("no partial $HOMEDIR", expand("$HOMEDIR/x"), "$HOMEDIR/x")
check("no partial $DEVNET_ROOTX", expand("$DEVNET_ROOTX"), "$DEVNET_ROOTX")

# A mid-word tilde is a literal.
check("mid-word ~", expand("/tmp/file~1"), "/tmp/file~1")

# Non-strings are returned unchanged.
check("None", expand(None), None)

print(f"config_test.py: {passed} assertions passed")

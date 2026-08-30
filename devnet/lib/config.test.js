// Tests for config value expansion. Run: node devnet/lib/config.test.js
const assert = require("assert");
const os = require("os");
const path = require("path");
const { expand, ROOT } = require("./config");

const HOME = os.homedir();
let passed = 0;

function check(name, actual, expected) {
  assert.strictEqual(actual, expected, `${name}: got ${actual}, want ${expected}`);
  passed++;
}

// $DEVNET_ROOT resolves to this directory, so entries pointing at scripts in
// this repository work regardless of where it was cloned.
check("$DEVNET_ROOT", expand("node $DEVNET_ROOT/step-recv.js"), `node ${ROOT}/step-recv.js`);
check("$DEVNET_ROOT twice",
  expand("$DEVNET_ROOT/a $DEVNET_ROOT/b"), `${ROOT}/a ${ROOT}/b`);

// $HOME and ~ both resolve to the user's home directory.
check("$HOME", expand("$HOME/devnet-workdir"), `${HOME}/devnet-workdir`);
check("~", expand("~/devnet-workdir"), `${HOME}/devnet-workdir`);
check("~ after a command", expand("python3 ~/x.py"), `python3 ${HOME}/x.py`);

// Literal values pass through untouched — placeholders the operator must edit,
// URLs, keyring names, hex constants.
check("placeholder", expand("/path/to/pqchaind"), "/path/to/pqchaind");
check("url", expand("tcp://127.0.0.1:26657"), "tcp://127.0.0.1:26657");
check("bare word", expand("validator"), "validator");
check("hex", expand("0x1260944489272988d9df"), "0x1260944489272988d9df");
check("empty", expand(""), "");

// Only exact tokens expand: a longer name that merely starts with one must not.
check("no partial $HOMEDIR", expand("$HOMEDIR/x"), "$HOMEDIR/x");
check("no partial $DEVNET_ROOTX", expand("$DEVNET_ROOTX"), "$DEVNET_ROOTX");

// A mid-word tilde is a literal, not a home directory.
check("mid-word ~", expand("/tmp/file~1"), "/tmp/file~1");

// Non-strings are returned unchanged.
check("undefined", expand(undefined), undefined);

console.log(`config.test.js: ${passed} assertions passed`);

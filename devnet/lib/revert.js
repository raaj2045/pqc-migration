// Decode the revert reason of a transaction that already failed on chain.
//
// A receipt only tells you status=0 and gasUsed — never WHY. Worse, gasUsed
// is a misleading progress signal: under EIP-7623 a transaction with large
// calldata is charged max(intrinsic + execution, calldata floor), so a relay
// tx carrying ~80KB of proof can burn >1M gas and report "2% of the limit"
// while having executed almost nothing. The only way to learn what actually
// rejected it is to re-execute the same call against the same block and read
// the returned revert data.
//
// The replay is an eth_call at the block the tx landed in, which runs against
// that block's post-state and header (so block.timestamp matches what the
// real execution saw — this matters for the light client's
// ALLOWED_SP1_CLOCK_DRIFT / timeout checks). `from` must be set or msg.sender
// changes and any access-controlled path decodes differently.
const { ethers, abi } = require("./lib");

// Errors the SP1 verifier and its generated gnark Groth16 verifier can throw.
// These are NOT in devnet/abi/*.json — those are only the IBC contracts — but
// _verifySP1Proof is exactly the kind of failure worth naming precisely.
// Source: ~/.sp1/circuits/groth16/<version>/{SP1VerifierGroth16,Groth16Verifier}.sol
const SP1_VERIFIER_ERRORS = [
  "error WrongVerifierSelector(bytes4 received, bytes4 expected)",
  "error InvalidExitCode()",
  "error InvalidProof()",
  "error InvalidVkRoot()",
  "error ProofInvalid()",
  "error PublicInputNotInField()",
];

// Every contract a relay tx can revert inside, nearest-first for reporting.
const ABI_SOURCES = ["ICS26Router", "SP1ICS07Tendermint", "ICS20Transfer", "Escrow", "IBCERC20"];

// Solidity's documented Panic codes (docs.soliditylang.org, "Panic via assert").
const PANIC_CODES = {
  "0": "generic compiler panic",
  "1": "assert(false) — a contract invariant was violated",
  "17": "arithmetic overflow/underflow",
  "18": "division or modulo by zero",
  "33": "invalid enum value",
  "34": "malformed storage byte array",
  "49": "pop() on an empty array",
  "50": "array index out of bounds",
  "65": "out of memory",
  "81": "call to an uninitialized internal function",
};

let cachedTable = null;

// selector -> { name, signature, contract, iface }
function errorTable() {
  if (cachedTable) return cachedTable;
  const table = new Map();
  const add = (contract, fragments) => {
    const iface = new ethers.Interface(fragments);
    iface.forEachError((frag) => {
      if (!table.has(frag.selector)) {
        table.set(frag.selector, { name: frag.name, signature: frag.format("full"), contract, iface });
      }
    });
  };
  for (const name of ABI_SOURCES) {
    let json;
    try {
      json = abi(name);
    } catch {
      continue; // an ABI this devnet doesn't ship is not fatal for decoding
    }
    add(name, (json.abi || json).filter((e) => e.type === "error"));
  }
  add("SP1Verifier", SP1_VERIFIER_ERRORS);
  // Solidity built-ins, so require(x, "msg") and assert failures decode too.
  add("solidity", ["error Error(string reason)", "error Panic(uint256 code)"]);
  cachedTable = table;
  return table;
}

// Pull revert bytes out of whatever shape ethers/the node wrapped them in.
function extractRevertData(e) {
  const candidates = [
    e && e.data,
    e && e.info && e.info.error && e.info.error.data,
    e && e.error && e.error.data,
    e && e.value,
  ];
  for (const c of candidates) {
    if (typeof c === "string" && /^0x[0-9a-fA-F]*$/.test(c)) return c;
    if (c && typeof c === "object" && typeof c.data === "string") return c.data;
  }
  return null;
}

// Decode raw revert data into a named custom error, if we know the selector.
function decodeRevertData(data) {
  if (!data || data === "0x") {
    // Empty revert data: revert()/require() with no reason, an invalid
    // opcode, or out-of-gas inside a call frame.
    return { selector: null, name: null, contract: null, text: "empty revert data (no reason string, bare revert or OOG)" };
  }
  const selector = data.slice(0, 10);
  const hit = errorTable().get(selector);
  if (!hit) {
    return { selector, name: null, contract: null, data, text: `unknown error selector ${selector}` };
  }
  let args = null;
  try {
    const decoded = hit.iface.parseError(data);
    args = decoded ? decoded.args.map((a) => (typeof a === "bigint" ? a.toString() : a)) : null;
  } catch {
    // Selector matched but the payload didn't decode — still report the name.
  }
  // Panic codes are the one case where the raw argument is useless on its
  // own — 0x01 (a failed assert) is what a contract throws when an
  // invariant it considered impossible was violated, e.g. SP1MockVerifier's
  // assert(proofBytes.length == 0) when handed a real proof.
  if (hit.name === "Panic" && args && args.length === 1) {
    const code = BigInt(args[0]);
    const meaning = PANIC_CODES[code.toString()] || "unknown panic code";
    return {
      selector, name: hit.name, contract: hit.contract, signature: hit.signature,
      args, data,
      text: `Panic(0x${code.toString(16).padStart(2, "0")}) — ${meaning}`,
    };
  }
  const argText = args && args.length ? `(${args.map((a) => JSON.stringify(a)).join(", ")})` : "()";
  return {
    selector,
    name: hit.name,
    contract: hit.contract,
    signature: hit.signature,
    args,
    data,
    text: `${hit.contract}.${hit.name}${argText}`,
  };
}

// Replay `txRequest` at `blockNumber` and return the decoded revert.
// Never throws: a replay that itself fails (pruned state, node without the
// block) is reported as such rather than masking the original failure.
async function captureRevert(provider, txRequest, blockNumber) {
  try {
    const ret = await provider.call({ ...txRequest, blockTag: blockNumber });
    // The call succeeded on replay even though the tx reverted on chain —
    // means the block's state no longer reproduces it (state changed, or the
    // node re-executed against a different context). Say so plainly.
    return {
      replayed: true,
      reverted: false,
      text: `replay at block ${blockNumber} did NOT revert (returned ${ret.slice(0, 66)}) — state no longer reproduces the failure`,
    };
  } catch (e) {
    const data = extractRevertData(e);
    if (data === null) {
      return {
        replayed: false,
        reverted: null,
        text: `replay at block ${blockNumber} failed without revert data: ${(e && e.message) || String(e)}`.slice(0, 400),
      };
    }
    return { replayed: true, reverted: true, blockNumber, ...decodeRevertData(data) };
  }
}

module.exports = { captureRevert, decodeRevertData, errorTable, extractRevertData, SP1_VERIFIER_ERRORS };

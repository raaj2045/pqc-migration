// Relay ONE bounded-size CHUNK of a larger group as one proof-api request /
// one EVM multicall transaction, signed by an explicit (usually pool)
// account rather than always DEPLOYER_PK — so relay_pool.py can run several
// of these concurrently, one per pool account, without them fighting over a
// shared nonce sequence.
//
// This generalizes the original single-account, whole-group relay approach
// (relay-batch.js, since removed — this fully superseded it) for the
// chunked/pooled relay design
// (see find_relay_ceiling.py and README.md's "Relay chunk size" section):
// a group larger than the empirically-measured per-tx packet ceiling (66
// packets, under go-ethereum's 128KB default tx-size cap) cannot be relayed
// in one transaction at all, chunk size or not — this script relays exactly
// the chunk it's given and nothing more; relay_pool.py is responsible for
// splitting a group into chunks that already fit.
//
// Difference from the original gas-split approach: rather than a best-
// effort debug_traceTransaction call trace (unreliable — Kurtosis geth does
// not always expose the debug namespace, and even when it does, matching
// call frames to selectors doesn't cleanly separate a fused proof), this
// reads the light client's OWN latestHeight from chain state immediately
// before and after this chunk's block — ground truth, not a guess.
//
// An earlier version of this script tried decoding the multicall's
// top-level bytes[] for a standalone updateClient(string,bytes) selector,
// on the theory that ICS26Router.multicall calls updateClient and
// recvPacket as separate sibling entries. That is wrong for how proof-api
// actually builds these calls: it uses SP1's combined
// "update_client_and_membership" program, so EVERY recvPacket call already
// carries its own consensus-height proof fused in — there is no separate
// top-level updateClient() call to find, ever, whether or not an update
// actually happened. Decoding for that selector silently always reports
// false, which looked like "no chunk ever updates" but was actually just
// measuring the wrong thing (confirmed empirically: see README.md's
// "Chunked relay: true vs the ideal" section). Reading
// clientState() at blockNumber-1 vs blockNumber sidesteps the whole
// question of which call structure carries the update — it just asks
// "did this chunk's landing block actually change the trusted height",
// which is the only thing that matters for the gas accounting.
//
// Usage: node relay-chunk.js <out-dir> [--signer-key=0x...] [--summary=name.json] <cosmos-tx-hash> [...]
//
// <out-dir> is shared across concurrently-running chunks (recv-result files
// are safe to co-locate: chain sequence numbers are globally unique, so
// concurrent chunks never write the same filename) — but each chunk's own
// summary file needs a unique name, hence --summary (default chunk-relay.json,
// fine for a single standalone chunk, but relay_pool.py always passes one).
const fs = require("fs");
const path = require("path");
const { loadEnv, evm, sendRawTx, cosmosCli, config, ethers } = require("../../devnet/lib/lib");
const proofapi = require("../../devnet/lib/proofapi");
const P = require("../../devnet/lib/packet");
const { captureRevert } = require("../../devnet/lib/revert");

function cosmosTx(hash) {
  return JSON.parse(cosmosCli(["query", "tx", hash, "-o", "json"]));
}

function fail(kind, message) {
  console.error(`RELAY_CHUNK_FAILURE kind=${kind} message=${JSON.stringify(message)}`);
  process.exit(1);
}

(async () => {
  const argv = process.argv.slice(2);
  const outDir = argv[0];
  const signerArg = argv.find((a) => a.startsWith("--signer-key="));
  const signerKey = signerArg && signerArg.split("=")[1];
  const summaryArg = argv.find((a) => a.startsWith("--summary="));
  const summaryName = (summaryArg && summaryArg.split("=")[1]) || "chunk-relay.json";
  const txHashes = argv.slice(1).filter((a) => !a.startsWith("--signer-key=") && !a.startsWith("--summary="));
  if (!outDir || txHashes.length === 0) {
    console.error("usage: node relay-chunk.js <out-dir> [--signer-key=0x...] <cosmos-tx-hash> [...]");
    process.exit(2);
  }
  fs.mkdirSync(outDir, { recursive: true });

  const env = loadEnv();
  config.require_(env, "CHAIN_ID", "ETH_CLIENT_ID", "COSMOS_CLIENT_ID", "PROOF_API_ADDR");
  const { provider, router, lc } = evm(env);
  const signer = signerKey ? new ethers.Wallet(signerKey, provider) : router.runner;

  // 1. Same per-packet validation as the original single-account relay.
  const bySeq = new Map();
  for (const hash of txHashes) {
    let tx;
    try {
      tx = cosmosTx(hash);
    } catch (e) {
      fail("cosmos_query_failed", `tx ${hash}: ${e.message}`.slice(0, 500));
    }
    const ev = tx.events.find((e) => e.type === "send_packet");
    if (!ev) fail("no_send_packet_event", `tx ${hash} has no send_packet event`);
    const hex = ev.attributes.find((a) => a.key === "encoded_packet_hex").value;
    const pkt = P.decodePacket(hex);
    if (pkt.sourceClient !== env.COSMOS_CLIENT_ID || pkt.destClient !== env.ETH_CLIENT_ID) {
      fail("client_mismatch",
        `packet clients (${pkt.sourceClient} -> ${pkt.destClient}) do not match ` +
        `COSMOS_CLIENT_ID/ETH_CLIENT_ID (${env.COSMOS_CLIENT_ID} -> ${env.ETH_CLIENT_ID})`);
    }
    const commitment = P.packetCommitment(pkt);
    const stored = JSON.parse(cosmosCli(["query", "ibc", "channelv2", "packet-commitment",
      pkt.sourceClient, String(pkt.sequence), "-o", "json"]));
    const storedHex = "0x" + Buffer.from(stored.commitment, "base64").toString("hex");
    if (storedHex !== commitment) {
      fail("commitment_mismatch", `seq ${pkt.sequence}: computed ${commitment} stored ${storedHex}`);
    }
    bySeq.set(pkt.sequence.toString(), { hash, pkt, hex });
  }

  // 2. ONE proof-api request for this chunk.
  const chainId = (await provider.getNetwork()).chainId.toString();
  const client = proofapi.connect(env);
  const started = Date.now();
  let relay;
  try {
    relay = await proofapi.relayByTx(client, {
      srcChain: env.CHAIN_ID,
      dstChain: chainId,
      sourceTxIds: txHashes.map((h) => Buffer.from(h, "hex")),
      srcClientId: env.COSMOS_CLIENT_ID,
      dstClientId: env.ETH_CLIENT_ID,
    });
  } catch (e) {
    fail("proof_api_error", e.message || String(e));
  }
  const proveSeconds = ((Date.now() - started) / 1000).toFixed(1);
  if (relay.address.toLowerCase() !== env.ICS26_ROUTER.toLowerCase()) {
    fail("wrong_target", `proof-api targets ${relay.address}, expected ICS26Router ${env.ICS26_ROUTER}`);
  }

  // 2b. Count recvPacket calls (accurate — see header comment on why
  // updateClient detection needs the post-submission state-diff below
  // instead of a selector match).
  const data = "0x" + Buffer.from(relay.tx).toString("hex");
  let recvCount = 0;
  try {
    const [innerCalls] = router.interface.decodeFunctionData("multicall", data);
    const recvSelector = router.interface.getFunction("recvPacket").selector;
    for (const inner of innerCalls) {
      if (inner.slice(0, 10) === recvSelector) recvCount++;
    }
  } catch (e) {
    // Not a multicall (e.g. proof-api returned a single call directly for a
    // 1-packet chunk) — leave recvCount as best-effort zero; total gas from
    // the receipt is unaffected either way.
  }

  // 3. Submit, with an EXPLICIT gasLimit. Without one, ethers falls back to
  //    eth_estimateGas, whose binary search calls eth_call at gas-starved
  //    candidate amounts; a call that runs out of gas mid-verification there
  //    can surface a misleading revert reason that has nothing to do with
  //    the tx's actual behavior at full gas.
  const block = await provider.getBlock("latest");
  const gasLimit = block.gasLimit - (block.gasLimit / 20n);
  let r;
  try {
    r = await sendRawTx(signer, relay.address, data, { gasLimit });
  } catch (e) {
    if (e && e.receipt) {
      r = e.receipt;
    } else {
      const msg = (e && e.message) || String(e);
      if (/gas required exceeds allowance|exceeds block gas limit|intrinsic gas too low/i.test(msg)) {
        fail("gas_limit_exceeded", msg.slice(0, 800));
      }
      if (/oversized data|tx size|exceeds the maximum/i.test(msg)) {
        fail("tx_size_exceeded", msg.slice(0, 800));
      }
      if (/timeout|ETIMEDOUT|ECONNRESET/i.test(msg)) {
        fail("timeout", msg.slice(0, 800));
      }
      fail("submit_error", msg.slice(0, 800));
    }
  }
  if (r.status !== 1) {
    // gasUsed alone cannot classify this failure. With ~80KB of proof
    // calldata, EIP-7623 charges max(intrinsic + execution, calldata floor),
    // so a tx that reverted on its very first check still reports over a
    // million gas — a "2% of the limit" ratio says nothing about how far
    // execution actually got. Replay the identical call at the block it
    // landed in and decode the revert data instead of guessing.
    const ratio = Number(r.gasUsed * 100n / gasLimit);
    const revert = await captureRevert(
      provider,
      { from: await signer.getAddress(), to: relay.address, data, gasLimit, value: 0 },
      r.blockNumber
    );
    // Persist the full diagnosis: the stderr line is truncated by callers,
    // and the chain state needed to reproduce it may not survive the run.
    const failureName = summaryName.replace(/\.json$/, "") + ".failure.json";
    fs.writeFileSync(path.join(outDir, failureName), JSON.stringify({
      txHash: r.hash,
      blockNumber: r.blockNumber,
      from: await signer.getAddress(),
      to: relay.address,
      gasUsed: r.gasUsed.toString(),
      gasLimit: gasLimit.toString(),
      gasUsedPercentOfLimit: ratio,
      calldataBytes: (data.length - 2) / 2,
      sequences: [...bySeq.keys()],
      proofHeight: relay.proofHeight ? String(relay.proofHeight) : undefined,
      revert,
    }, null, 2));
    // Only call it gas exhaustion if execution genuinely reached the limit —
    // the EIP-7623 floor can push gasUsed high on a tx that never came close.
    const kind = ratio >= 99 ? "gas_limit_exceeded" : "reverted";
    const why = revert.text || "revert reason not recovered";
    fail(kind, `relay tx ${r.hash} reverted in block ${r.blockNumber}: ${why} ` +
      `[gas ${r.gasUsed} of limit ${gasLimit} (${ratio}%), ${(data.length - 2) / 2} bytes calldata; ` +
      `see ${failureName}]`);
  }

  // 3b. Ground truth for whether THIS chunk's transaction actually advanced
  // the client's trusted height — read from the two blocks straddling it,
  // not from anything in the calldata (see header comment). Immune to
  // concurrent chunks racing each other: this only ever looks at the exact
  // block this tx landed in, regardless of what any other chunk did.
  const revisionHeight = (cs) => cs[2][1];
  const [csBefore, csAfter] = await Promise.all([
    lc.clientState({ blockTag: r.blockNumber - 1 }),
    lc.clientState({ blockTag: r.blockNumber }),
  ]);
  const heightBefore = revisionHeight(csBefore);
  const heightAfter = revisionHeight(csAfter);
  const updateIncluded = heightAfter > heightBefore;

  // 4. Correlate acks, write recv-result files (consumed by devnet/step-ack.js).
  const acksBySeq = new Map();
  for (const log of r.logs) {
    let parsed;
    try {
      parsed = router.interface.parseLog(log);
    } catch {
      continue;
    }
    if (parsed && parsed.name === "WriteAcknowledgement") {
      acksBySeq.set(parsed.args.sequence.toString(), parsed.args.acknowledgements[0]);
    }
  }
  const missing = [...bySeq.keys()].filter((s) => !acksBySeq.has(s));
  if (missing.length > 0) {
    fail("missing_acks", `no WriteAcknowledgement for sequence(s) ${missing.join(",")}`);
  }

  const txHashToSeq = {};
  for (const [seq, { pkt, hex, hash }] of bySeq) {
    const ackHex = acksBySeq.get(seq);
    fs.writeFileSync(path.join(outDir, `recv-result-${seq}.json`), JSON.stringify({
      sequence: seq,
      ack: ackHex,
      ackBlockNumber: String(r.blockNumber),
      packetHex: hex,
    }, null, 2));
    txHashToSeq[hash.toUpperCase()] = seq;
  }

  fs.writeFileSync(path.join(outDir, summaryName), JSON.stringify({
    groupSize: bySeq.size,
    sequences: [...bySeq.keys()],
    txHashToSeq,
    txHash: r.hash,
    signerAddress: await signer.getAddress(),
    blockNumber: r.blockNumber,
    totalGas: r.gasUsed.toString(),
    proveSeconds: Number(proveSeconds),
    relayTxBytes: relay.tx.length,
    updateIncluded,
    heightBefore: heightBefore.toString(),
    heightAfter: heightAfter.toString(),
    recvCount,
  }, null, 2));
  console.log(`chunk of ${bySeq.size}: gas ${r.gasUsed}, updateIncluded=${updateIncluded}, ` +
    `wrote to ${outDir}`);
})().catch((e) => fail("unexpected_error", (e && e.stack) || String(e)));

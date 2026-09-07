// PHASE 2 of the Ethereum -> Cosmos migration flow: relay a BATCH of packets
// from the EVM to Cosmos as ONE Cosmos transaction.
//
// This is the leg that carries real consensus verification on this direction:
// cw-ics08-wasm-eth BLS-verifies the sync committee in MsgUpdateClient, then
// verifies each packet's MPT membership proof against the execution state
// root it holds for the proven slot. There is no proof-api and no multicall
// here — the batching primitives are:
//
//   ONE eth_getProof with N storage keys   (the account proof is shared;
//                                           each packet gets its own storage
//                                           proof from the same response)
//   ONE MsgUpdateClient                     (explicit, separately submitted —
//                                           unlike the other direction, where
//                                           proof-api fuses it invisibly)
//   ONE Cosmos tx of N MsgRecvPacket        (sendtx.py already accepts a JSON
//                                           list of messages)
//
// --no-update skips the client update, to test whether an ALREADY-held
// consensus state covers a later batch (Phase 0 unknown 3).
//
// The message signer is resolved LIVE from the keyring (lib.js's
// signerAddress), never from a pinned address: a Cosmos tx whose declared
// signer is not the key that actually signed it is rejected by the ante
// handler.
//
// --signer-key=NAME signs BOTH the MsgUpdateClient and the receive tx with
// that keyring key instead of RELAYER_KEY. This is the key-type axis: a
// signature is charged once per transaction, so an ML-DSA-65 signer adds a
// fixed ~5.2 KB / ~146k gas per tx that batching amortizes (see
// CEILING-FINDINGS.md). The DESTINATION key type is not an axis — receivers
// appear in the payload as 20-byte bech32 addresses whatever key would
// control them, and a fresh recipient holds no pubkey on chain at all.
//
// Timing recorded here is measured, not inferred:
//   finalityWaitSeconds  real time spent blocked on beacon finality covering
//                        the newest send block (0 if already covered on entry)
//   updateSeconds / proofSeconds / txSeconds   the three relay phases
//   creditedTs           the TIMESTAMP OF THE COSMOS BLOCK that credited the
//                        vouchers, not this process's wall clock
//
// Usage: node relay-recv-batch.js <send-file> --count=N [--offset=K]
//                                 [--no-update] [--gas=N] [--label=name]
//                                 [--signer-key=NAME]
//                                 [--finality-timeout=SECONDS]  (default 3600)
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");
const { loadEnv, evm, ethers, config, signerAddress, signerAlgo } = require("../../devnet/lib/lib");
const P = require("../../devnet/lib/packet");

const arg = (name, dflt) => {
  const a = process.argv.find((x) => x.startsWith(`--${name}=`));
  return a ? a.split("=").slice(1).join("=") : dflt;
};
const flag = (name) => process.argv.includes(`--${name}`);
const b64 = (hex) => Buffer.from(hex.replace(/^0x/, ""), "hex").toString("base64");
const get = (url) => JSON.parse(execFileSync("curl", ["-s", "-m", "30", url], { maxBuffer: 64e6 }));

(async () => {
  const sendFile = process.argv[2];
  if (!sendFile || !fs.existsSync(sendFile)) {
    console.error("usage: node relay-recv-batch.js <send-file> --count=N [--offset=K] [--no-update] [--gas=N] [--label=name] [--signer-key=NAME]");
    process.exit(2);
  }
  const count = parseInt(arg("count", "1"), 10);
  const offset = parseInt(arg("offset", "0"), 10);
  const label = arg("label", `n${count}`);
  const doUpdate = !flag("no-update");
  const signerKey = arg("signer-key", null) || undefined;

  const env = loadEnv();
  config.require_(env, "COSMOS_CLIENT_ID", "ICS26_ROUTER", "IBC_COMMITMENT_SLOT",
    "SENDTX_CMD", "UPDATE_CLIENT_CMD", "RELAYER_KEY", "BEACON_URL", "GETH_RPC");
  const { router } = evm(env);

  const send = JSON.parse(fs.readFileSync(sendFile, "utf8"));
  const all = send.packets.filter((p) => p.status === "committed");
  const batch = all.slice(offset, offset + count);
  if (batch.length !== count) throw new Error(`only ${batch.length} packets available at offset ${offset}, need ${count}`);
  console.log(`batch "${label}": ${count} packet(s), sequences ${batch.map((p) => p.sequence).join(",")}`);

  // --- storage keys: one per packet, all under ICS26Router -----------------
  const slotHex = ethers.zeroPadValue(env.IBC_COMMITMENT_SLOT, 32);
  const keys = batch.map((p) => {
    const ibcPath = P.packetCommitmentKey(p.sourceClient, BigInt(p.sequence));
    return ethers.keccak256(ethers.concat([ethers.keccak256(ibcPath), slotHex]));
  });
  // Confirm every commitment is still live on the EVM before proving.
  for (const p of batch) {
    const onchain = await router.getCommitment(
      ethers.keccak256(P.packetCommitmentKey(p.sourceClient, BigInt(p.sequence))));
    if (onchain !== p.commitment) throw new Error(`seq ${p.sequence}: commitment drifted (${onchain})`);
  }

  // --- wait for finality to cover the newest send block in this batch ------
  // This wait is MEASURED, not derived by subtracting the other phases from a
  // total: it is the dominant term on this direction and the one the graph
  // labels, so it has to be the real thing.
  const maxSendBlock = Math.max(...batch.map((p) => p.blockNumber));
  const beacon = env.BEACON_URL.replace(/\/$/, "");
  // Bounded by a DEADLINE, not a poll count. Beacon finality advances one
  // epoch (32 slots) at a time, so covering a send block can need up to three
  // advances — ~19 min at 12 s slots — and a poll-count cap tuned for a
  // typical wait aborts the run on an atypical one.
  const finalityTimeout = parseInt(arg("finality-timeout", "3600"), 10) * 1000;
  const t_finality0 = Date.now();
  let hdr, finalityPolls = 0, lastExec = null;
  while (Date.now() - t_finality0 < finalityTimeout) {
    const fin = get(`${beacon}/eth/v1/beacon/light_client/finality_update`).data;
    finalityPolls++;
    const execNum = Number(fin.finalized_header.execution.block_number);
    if (execNum >= maxSendBlock) { hdr = fin.finalized_header; break; }
    if (execNum !== lastExec) {
      const epochs = Math.ceil((maxSendBlock - execNum) / 32);
      console.log(`  waiting for finality to cover block ${maxSendBlock} (at ${execNum}) ` +
        `— ~${epochs} epoch advance(s), up to ${(epochs * 32 * 12 / 60).toFixed(1)} min`);
      lastExec = execNum;
    }
    execFileSync("sleep", ["12"]);
  }
  const finalityWaitSeconds = (Date.now() - t_finality0) / 1000;
  if (!hdr) {
    throw new Error(`finality did not reach block ${maxSendBlock} within ` +
      `${(finalityTimeout / 60000).toFixed(0)} min (last seen ${lastExec}); ` +
      `raise --finality-timeout=SECONDS`);
  }
  const proofSlot = Number(hdr.beacon.slot);
  console.log(`  finality wait: ${finalityWaitSeconds.toFixed(1)}s over ${finalityPolls} poll(s), ` +
    `covering send block ${maxSendBlock} at slot ${proofSlot}`);

  // --- one MsgUpdateClient (or none, if testing coverage of an old state) --
  const t_update0 = Date.now();
  let updateGas = 0, updateDid = false;
  const cli = (args) => JSON.parse(execFileSync(env.PQCHAIND_BIN,
    [...args, "--home", env.CHAIN_HOME, "--node", env.CHAIN_NODE, "-o", "json"],
    { encoding: "utf8", maxBuffer: 64e6 }));
  const heights = () => cli(["query", "ibc", "client", "consensus-states", env.COSMOS_CLIENT_ID])
    .consensus_states.map((e) => Number(e.height.revision_height));

  if (doUpdate) {
    const [updCmd, ...updArgs] = env.UPDATE_CLIENT_CMD.split(/\s+/);
    const updSigner = signerKey ? [`--signer-key=${signerKey}`] : [];
    const out = execFileSync(updCmd, [...updArgs, env.COSMOS_CLIENT_ID, ...updSigner], { encoding: "utf8" });
    process.stdout.write(out.split("\n").map((l) => `  [update] ${l}`).join("\n") + "\n");
    for (const line of out.split("\n")) {
      const t = line.trim();
      if (t.startsWith("{") && t.includes("gas_used")) {
        try { updateGas = parseInt(JSON.parse(t).gas_used, 10); updateDid = true; } catch { /* not the result line */ }
      }
    }
  } else {
    console.log("  [update] skipped (--no-update): testing coverage by an existing consensus state");
  }
  const updateSeconds = (Date.now() - t_update0) / 1000;

  const have = heights();
  const usable = have.filter((s) => s >= proofSlot);
  if (!usable.length) {
    throw new Error(`no consensus state at or after slot ${proofSlot}; have [${have.join(",")}]`);
  }
  // NEWEST usable state, not the oldest: geth keeps only ~128 blocks of
  // historical state (TriesInMemory), so an older state names a block whose
  // state may already be pruned and eth_getProof then returns nothing. Same
  // reasoning as build-recv-msgs.js.
  const useSlot = Math.max(...usable);
  const blk = get(`${beacon}/eth/v2/beacon/blocks/${useSlot}`);
  const useBlock = Number(blk.data.message.body.execution_payload.block_number);
  console.log(`  proving at slot ${useSlot} (execution block ${useBlock}); client holds ${have.length} state(s)`);

  // --- ONE eth_getProof for all N storage keys -----------------------------
  const rpc = (env.GETH_RPC || "").replace(/^https?:\/\//, "");
  const t_proof0 = Date.now();
  const proof = JSON.parse(execFileSync("curl", [
    "-s", "-m", "120", "-X", "POST", "-H", "Content-Type: application/json",
    "--data", JSON.stringify({
      jsonrpc: "2.0", method: "eth_getProof",
      params: [env.ICS26_ROUTER, keys, "0x" + useBlock.toString(16)], id: 1,
    }), `http://${rpc}`,
  ], { maxBuffer: 256e6 })).result;
  const proofSeconds = (Date.now() - t_proof0) / 1000;
  if (!proof || !proof.storageProof || proof.storageProof.length !== count) {
    throw new Error(`eth_getProof returned ${proof && proof.storageProof ? proof.storageProof.length : "no"} storage proofs, expected ${count}`);
  }
  const accountProofBytes = proof.accountProof.join("").length / 2;
  const storageProofBytes = proof.storageProof.map((sp) => sp.proof.join("").length / 2);
  console.log(`  eth_getProof: ${count} storage proof(s) in ${proofSeconds.toFixed(2)}s ` +
    `(account proof ~${accountProofBytes}B, storage proofs ~${storageProofBytes.reduce((a, b) => a + b, 0)}B total)`);

  for (let i = 0; i < count; i++) {
    if (proof.storageProof[i].value === "0x0") {
      throw new Error(`seq ${batch[i].sequence}: storage value is 0x0 at block ${useBlock} — commitment not present`);
    }
  }

  // --- N MsgRecvPacket in ONE Cosmos tx ------------------------------------
  // Signer resolved live from the keyring, never from a pinned address.
  const signer = signerAddress(env, signerKey);

  const msgs = batch.map((p, i) => {
    const sp = proof.storageProof[i];
    const membershipProof = {
      account_proof: { storage_root: proof.storageHash, proof: proof.accountProof },
      storage_proof: { key: ethers.zeroPadValue(sp.key, 32), value: sp.value, proof: sp.proof },
    };
    return {
      "@type": "/ibc.core.channel.v2.MsgRecvPacket",
      packet: {
        sequence: p.sequence,
        source_client: p.sourceClient,
        destination_client: p.destClient,
        timeout_timestamp: p.timeoutTimestamp,
        payloads: [{
          source_port: p.payload.sourcePort,
          destination_port: p.payload.destPort,
          version: p.payload.version,
          encoding: p.payload.encoding,
          value: b64(p.payload.value),
        }],
      },
      proof_commitment: Buffer.from(JSON.stringify(membershipProof)).toString("base64"),
      proof_height: { revision_number: "0", revision_height: String(useSlot) },
      signer,
    };
  });

  const outDir = path.join(env.DEVNET_DIR, "migration-volume");
  fs.mkdirSync(outDir, { recursive: true });
  const msgFile = path.join(outDir, `msg-recv-${label}.json`);
  fs.writeFileSync(msgFile, JSON.stringify(msgs));
  const msgFileBytes = fs.statSync(msgFile).size;
  console.log(`  built ${count} MsgRecvPacket (${msgFileBytes}B of JSON) -> ${path.basename(msgFile)}`);

  const gas = arg("gas", String(400_000 + 900_000 * count));
  const [txCmd, ...txArgs] = env.SENDTX_CMD.split(/\s+/);
  const t_tx0 = Date.now();
  let out, failed = false;
  try {
    out = execFileSync(txCmd, [...txArgs, msgFile, signerKey || env.RELAYER_KEY, gas],
      { encoding: "utf8", maxBuffer: 64e6 });
  } catch (e) {
    failed = true;
    out = (e.stdout || "") + (e.stderr || "");
  }
  const txSeconds = (Date.now() - t_tx0) / 1000;
  const creditedWallTs = Date.now() / 1000;
  process.stdout.write(out.split("\n").filter(Boolean).map((l) => `  [recv] ${l}`).join("\n") + "\n");

  let result = null;
  for (const line of out.split("\n")) {
    const t = line.trim();
    if (t.startsWith("{") && t.includes("gas_used")) { try { result = JSON.parse(t); } catch { /* skip */ } }
  }
  const ok = !failed && result && result.code === 0;

  // The whole cohort is credited in ONE Cosmos block, so the credit time is
  // that block's timestamp — an exact instant, not this process's wall clock
  // after a polling subprocess returned. Only the wall clock is kept as a
  // fallback, and it is recorded separately rather than silently substituted.
  let creditedTs = null, creditedHeight = null;
  if (ok && result.height) {
    creditedHeight = parseInt(result.height, 10);
    const rpc = env.CHAIN_NODE.replace(/^tcp:/, "http:");
    const blk = get(`${rpc}/block?height=${creditedHeight}`);
    creditedTs = new Date(blk.result.block.header.time).getTime() / 1000;
  }

  const signerAlgorithm = signerAlgo(env, signerKey);
  const summary = {
    label, count, offset, sequences: batch.map((p) => p.sequence),
    ok,
    signerKey: signerKey || env.RELAYER_KEY,
    signerAddress: signer,
    signerAlgo: signerAlgorithm,
    useSlot, useBlock, proofSlot,
    updateSubmitted: updateDid, updateGas, updateSeconds,
    finalityWaitSeconds, finalityPolls, maxSendBlock,
    proofSeconds, accountProofBytes,
    storageProofBytesTotal: storageProofBytes.reduce((a, b) => a + b, 0),
    msgJsonBytes: msgFileBytes,
    gasLimit: parseInt(gas, 10),
    txSeconds, creditedTs, creditedHeight, creditedWallTs,
    txhash: result && result.txhash,
    gasUsed: result && parseInt(result.gas_used, 10),
    gasWanted: result && parseInt(result.gas_wanted, 10),
    txBytes: result && result.tx_bytes,
    code: result && result.code,
    rawLog: result && result.raw_log ? String(result.raw_log).slice(0, 400) : undefined,
    consensusStatesHeld: have.length,
  };

  const sumFile = path.join(outDir, `recv-${label}.json`);
  fs.writeFileSync(sumFile, JSON.stringify(summary, null, 2));

  // Every number downstream depends on these having actually parsed out of the
  // tx result. A missing one used to read as 0 and propagate as a plausible
  // measurement, so fail here instead. The summary is already on disk at this
  // point, so a failure leaves the evidence behind rather than swallowing it.
  if (ok) {
    for (const k of ["gasUsed", "gasWanted", "txBytes"]) {
      if (!Number.isFinite(summary[k]) || summary[k] <= 0) {
        throw new Error(`tx succeeded but ${k} did not parse (${summary[k]}) — refusing to record a batch with unparseable gas/size`);
      }
    }
    if (updateDid && !(updateGas > 0)) {
      throw new Error(`MsgUpdateClient was submitted but its gas_used did not parse (${updateGas})`);
    }
    if (creditedTs === null) {
      throw new Error(`tx ${summary.txhash} succeeded but its block timestamp could not be read at height ${creditedHeight}`);
    }
  }
  console.log(`\n  RESULT ${summary.ok ? "OK" : "FAILED"}: ` +
    `gas_used=${summary.gasUsed} tx_bytes=${summary.txBytes} ` +
    `update_gas=${updateGas} (${updateDid ? "1 update" : "no update"}) ` +
    `signer=${summary.signerKey}/${signerAlgorithm}`);
  console.log(`  wrote ${sumFile}`);
  if (!ok) process.exit(1);
})().catch((e) => { console.error(e.message || e); process.exit(1); });

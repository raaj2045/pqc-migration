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
// consensus state covers a later batch.
//
// The message signer is resolved LIVE from the keyring (lib.js's
// signerAddress), never from a pinned address: a Cosmos tx whose declared
// signer is not the key that actually signed it is rejected by the ante
// handler.
//
// --signer-key=NAME signs BOTH the MsgUpdateClient and the receive tx with
// that keyring key instead of RELAYER_KEY. This is the key-type axis: a
// signature is charged once per transaction, so an ML-DSA-65 signer adds a
// fixed ~5.2 KB / ~146k gas per tx that a larger batch splits down (see
// LIMITS.md). The DESTINATION key type is not an axis — receivers
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
//                                 [--chunk-margin=FRACTION]     (default 0.90)
//                                 [--phase=all|prepare|deliver] [--use-slot=N]
//
// A batch too large for one transaction is split. The chunk size is derived
// from the node's OWN max_body_bytes/max_tx_bytes and from a real encoded
// transaction, not from a hard-coded packet count: per-packet cost is mostly
// Merkle-Patricia proof data and grows as the router's storage trie deepens,
// so a fixed count goes stale. Gas and bytes are summed across the chunks.
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

// --- how many MsgRecvPacket fit in one transaction -------------------------
// Read from the node's own config rather than assumed: `pqchaind tx broadcast`
// base64-encodes the tx into a JSON-RPC body, so the RPC's max_body_bytes caps
// the raw tx at 3/4 of its value, and the mempool's max_tx_bytes caps it
// outright. Whichever is smaller binds. Both are node configuration and both
// move between deployments.
function txSizeLimit(env) {
  const toml = path.join(env.CHAIN_HOME, "config", "config.toml");
  let maxBody = null, maxTx = null;
  for (const raw of fs.readFileSync(toml, "utf8").split("\n")) {
    const line = raw.split("#")[0].trim();
    let m;
    if ((m = line.match(/^max_body_bytes\s*=\s*(\d+)/))) maxBody = parseInt(m[1], 10);
    else if ((m = line.match(/^max_tx_bytes\s*=\s*(\d+)/))) maxTx = parseInt(m[1], 10);
  }
  if (!maxBody || !maxTx) throw new Error(`could not read max_body_bytes/max_tx_bytes from ${toml}`);
  const rpcRaw = Math.floor(maxBody * 3 / 4);
  return rpcRaw < maxTx
    ? { limit: rpcRaw, binding: `RPC max_body_bytes ${maxBody}` }
    : { limit: maxTx, binding: `mempool max_tx_bytes ${maxTx}` };
}

// Encoded size of a signed tx carrying `msgs`, measured rather than estimated.
// The per-packet cost is mostly Merkle-Patricia proof data and moves as the
// router's storage trie deepens, so a hard-coded packet count goes stale.
function encodedSize(env, msgs, signerKeyName, gas) {
  const tmp = path.join(env.DEVNET_DIR, "migration-volume", `.sizeprobe-${process.pid}.json`);
  fs.writeFileSync(tmp, JSON.stringify({
    body: { messages: msgs, memo: "", timeout_height: "0",
            extension_options: [], non_critical_extension_options: [] },
    auth_info: { signer_infos: [],
                 fee: { amount: [{ denom: "stake", amount: "6000" }],
                        gas_limit: String(gas), payer: "", granter: "" } },
    signatures: [],
  }));
  // --output-document must be a REAL FILE, not /dev/stdout: this runs as a
  // child process with a piped stdout, where opening /dev/stdout fails with
  // "no such device or address".
  const sf = tmp.replace(".json", ".signed.json");
  try {
    execFileSync(env.PQCHAIND_BIN,
      ["tx", "sign", tmp, "--from", signerKeyName, "--chain-id", env.CHAIN_ID,
        "--keyring-backend", "test", "--home", env.CHAIN_HOME, "--node", env.CHAIN_NODE,
        "--output-document", sf], { encoding: "utf8", maxBuffer: 256e6 });
    const enc = execFileSync(env.PQCHAIND_BIN, ["tx", "encode", sf, "--home", env.CHAIN_HOME],
      { encoding: "utf8", maxBuffer: 256e6 }).trim();
    return Buffer.from(enc, "base64").length;
  } finally {
    for (const f of [tmp, sf]) if (fs.existsSync(f)) fs.unlinkSync(f);
  }
}

// Largest chunk that fits under `limit` with a margin, found by measuring one
// full-size candidate and scaling. Verified by re-measuring before returning,
// so a non-linear surprise shrinks the chunk instead of failing a broadcast.
function chunkSize(env, msgs, signerKeyName, gasFor, limit, margin, log, jsonBytes) {
  if (!msgs.length) return 0;
  // Signing is not free — ML-DSA-65 especially — so skip the probe when the
  // batch cannot possibly need splitting. The proto encoding is SMALLER than
  // the JSON it comes from (proofs are base64 in JSON, raw bytes on the wire),
  // so the JSON size plus room for the largest signature is a safe bound.
  const target = Math.floor(limit * margin);
  if (jsonBytes && jsonBytes + 16_000 <= target) {
    log(`  one transaction fits: ${jsonBytes.toLocaleString()} B of JSON, ` +
      `bound by ${target.toLocaleString()} B usable (no size probe needed)`);
    return msgs.length;
  }
  const full = encodedSize(env, msgs, signerKeyName, gasFor(msgs.length));
  if (full <= target) {
    log(`  one transaction fits: ${full.toLocaleString()} B of ${target.toLocaleString()} B usable`);
    return msgs.length;
  }
  const perMsg = full / msgs.length;
  let n = Math.max(1, Math.floor(target / perMsg));
  for (let i = 0; i < 6 && n > 1; i++) {
    const got = encodedSize(env, msgs.slice(0, n), signerKeyName, gasFor(n));
    if (got <= target) {
      log(`  chunk size ${n}: ${got.toLocaleString()} B of ${target.toLocaleString()} B usable ` +
        `(~${Math.round(got / n).toLocaleString()} B per packet)`);
      return n;
    }
    n = Math.max(1, Math.floor(n * target / got) - 1);
  }
  return Math.max(1, n);
}

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
  // Waiting for finality and updating the light client are SHARED work: every
  // flow relaying from the same Ethereum block wants the same consensus state,
  // and two flows updating the client at once is a race for no benefit.
  //   prepare  wait for finality, update the client, stop
  //   deliver  assume a usable consensus state exists, build and deliver
  //   all      both, in one process (the default; what a lone flow does)
  const phase = arg("phase", "all");
  if (!["all", "prepare", "deliver"].includes(phase)) {
    throw new Error(`--phase must be all, prepare or deliver (got ${phase})`);
  }

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
  while (phase !== "deliver" && Date.now() - t_finality0 < finalityTimeout) {
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
  let finalityWaitSeconds = (Date.now() - t_finality0) / 1000;
  if (phase === "deliver") {
    // The shared prepare step already waited and already updated the client,
    // so this flow pays no wait. Do NOT re-read current finality to pick a
    // slot: finality keeps advancing, and asking for a state at the CURRENT
    // finalized slot demands one newer than prepare created. What matters is
    // only that a held state covers this batch's send block.
    finalityWaitSeconds = 0;
  }
  if (phase !== "deliver" && !hdr) {
    throw new Error(`finality did not reach block ${maxSendBlock} within ` +
      `${(finalityTimeout / 60000).toFixed(0)} min (last seen ${lastExec}); ` +
      `raise --finality-timeout=SECONDS`);
  }
  const proofSlot = hdr ? Number(hdr.beacon.slot) : null;
  if (phase !== "deliver") {
    console.log(`  finality wait: ${finalityWaitSeconds.toFixed(1)}s over ${finalityPolls} poll(s), ` +
      `covering send block ${maxSendBlock} at slot ${proofSlot}`);
  }

  // --- one MsgUpdateClient (or none, if testing coverage of an old state) --
  const t_update0 = Date.now();
  let updateGas = 0, updateDid = false;
  const cli = (args) => JSON.parse(execFileSync(env.PQCHAIND_BIN,
    [...args, "--home", env.CHAIN_HOME, "--node", env.CHAIN_NODE, "-o", "json"],
    { encoding: "utf8", maxBuffer: 64e6 }));
  const heights = () => cli(["query", "ibc", "client", "consensus-states", env.COSMOS_CLIENT_ID])
    .consensus_states.map((e) => Number(e.height.revision_height));

  if (doUpdate && phase !== "deliver") {
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
    console.log(`  [update] skipped (${phase === "deliver" ? "--phase=deliver" : "--no-update"}): ` +
      `relying on a consensus state already held`);
  }
  const updateSeconds = (Date.now() - t_update0) / 1000;

  const outDir_ = path.join(env.DEVNET_DIR, "migration-volume");
  fs.mkdirSync(outDir_, { recursive: true });
  const have = heights();
  const execBlockOf = (slot) =>
    Number(get(`${beacon}/eth/v2/beacon/blocks/${slot}`).data.message.body.execution_payload.block_number);

  // NEWEST state that works, not the oldest: geth keeps only ~128 blocks of
  // historical state (TriesInMemory), so an older state names a block whose
  // state may already be pruned and eth_getProof then returns nothing. Same
  // reasoning as build-recv-msgs.js.
  let useSlot;
  const pinned = arg("use-slot", null);
  if (pinned) {
    // Every flow in a wave proves against the SAME state, so their proof costs
    // are comparable.
    useSlot = parseInt(pinned, 10);
    if (!have.includes(useSlot)) {
      throw new Error(`--use-slot=${useSlot} is not held; have [${have.join(",")}]`);
    }
  } else if (phase === "deliver") {
    // Coverage of this batch's send block is the only requirement.
    useSlot = [...have].sort((a, b) => b - a).find((sl) => execBlockOf(sl) >= maxSendBlock);
    if (useSlot === undefined) {
      throw new Error(`no consensus state covering send block ${maxSendBlock}; ` +
        `have [${have.join(",")}] — run --phase=prepare first`);
    }
  } else {
    const usable = have.filter((sl) => sl >= proofSlot);
    if (!usable.length) {
      throw new Error(`no consensus state at or after slot ${proofSlot}; have [${have.join(",")}]`);
    }
    useSlot = Math.max(...usable);
  }
  if (phase === "prepare") {
    // Shared work is done. Record what it cost so the flows that follow can be
    // charged for it once between them rather than once each.
    const prep = {
      label, phase, maxSendBlock, proofSlot, useSlot,
      finalityWaitSeconds, finalityPolls,
      updateSubmitted: updateDid, updateGas, updateSeconds,
      consensusStatesHeld: have.length,
    };
    const prepFile = path.join(outDir_, `prepare-${label}.json`);
    fs.writeFileSync(prepFile, JSON.stringify(prep, null, 2));
    console.log(`\n  PREPARED: finality ${finalityWaitSeconds.toFixed(1)}s, ` +
      `update ${updateDid ? updateGas + " gas" : "not needed"}, usable slot ${useSlot}`);
    console.log(`  wrote ${prepFile}`);
    return;
  }
  const blk = get(`${beacon}/eth/v2/beacon/blocks/${useSlot}`);
  const useBlock = Number(blk.data.message.body.execution_payload.block_number);
  console.log(`  proving at slot ${useSlot} (execution block ${useBlock}); client holds ${have.length} state(s)`);

  // --- ONE eth_getProof for all N storage keys -----------------------------
  const rpc = (env.GETH_RPC || "").replace(/^https?:\/\//, "");
  const t_proof0 = Date.now();
  const proofResp = JSON.parse(execFileSync("curl", [
    "-s", "-m", "120", "-X", "POST", "-H", "Content-Type: application/json",
    "--data", JSON.stringify({
      jsonrpc: "2.0", method: "eth_getProof",
      params: [env.ICS26_ROUTER, keys, "0x" + useBlock.toString(16)], id: 1,
    }), `http://${rpc}`,
  ], { maxBuffer: 256e6 }));
  const proofSeconds = (Date.now() - t_proof0) / 1000;
  if (proofResp && proofResp.error) {
    // geth runs --gcmode=full with TriesInMemory=128, so state older than
    // ~128 blocks is gone. Relaying has to happen inside the window between
    // finality covering the send block (~70 blocks back) and that pruning
    // point — roughly 58 blocks, about 12 minutes.
    const msg = proofResp.error.message || JSON.stringify(proofResp.error);
    if (/historical state|not available/i.test(msg)) {
      throw new Error(`eth_getProof: block ${useBlock} has been pruned by geth (${msg}). ` +
        `These packets can no longer be proven; they must be re-sent. Relay promptly ` +
        `after finality — the usable window is ~58 blocks.`);
    }
    throw new Error(`eth_getProof failed at block ${useBlock}: ${msg}`);
  }
  const proof = proofResp && proofResp.result;
  if (!proof || !proof.storageProof || proof.storageProof.length !== count) {
    throw new Error(`eth_getProof returned ${proof && proof.storageProof ? proof.storageProof.length : "no"} storage proofs, expected ${count} at block ${useBlock}`);
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

  const outDir = outDir_;
  const msgFile = path.join(outDir, `msg-recv-${label}.json`);
  fs.writeFileSync(msgFile, JSON.stringify(msgs));
  const msgFileBytes = fs.statSync(msgFile).size;
  console.log(`  built ${count} MsgRecvPacket (${msgFileBytes}B of JSON) -> ${path.basename(msgFile)}`);

  // --- split into as few transactions as the node's limits allow -----------
  const margin = parseFloat(arg("chunk-margin", "0.90"));
  const { limit, binding } = txSizeLimit(env);
  const gasFor = (k) => parseInt(arg("gas", String(400_000 + 900_000 * k)), 10);
  console.log(`  size limit: ${limit.toLocaleString()} B usable (${binding}), margin ${margin}`);
  const perChunk = chunkSize(env, msgs, signerKey || env.RELAYER_KEY, gasFor,
    limit, margin, (m) => console.log(m), msgFileBytes);
  const chunks = [];
  for (let i = 0; i < msgs.length; i += perChunk) chunks.push(msgs.slice(i, i + perChunk));
  if (chunks.length > 1) {
    console.log(`  ${count} packet(s) -> ${chunks.length} transaction(s) of <= ${perChunk}`);
  }

  const [txCmd, ...txArgs] = env.SENDTX_CMD.split(/\s+/);
  const t_tx0 = Date.now();
  const parts = [];
  let failed = false, out = "";
  for (let ci = 0; ci < chunks.length && !failed; ci++) {
    const chunkFile = path.join(outDir,
      chunks.length > 1 ? `msg-recv-${label}-c${ci}.json` : `msg-recv-${label}.json`);
    fs.writeFileSync(chunkFile, JSON.stringify(chunks[ci]));
    const gas = String(gasFor(chunks[ci].length));
    let cOut;
    try {
      cOut = execFileSync(txCmd, [...txArgs, chunkFile, signerKey || env.RELAYER_KEY, gas],
        { encoding: "utf8", maxBuffer: 64e6 });
    } catch (e) {
      failed = true;
      cOut = (e.stdout || "") + (e.stderr || "");
    }
    out += cOut;
    let r = null;
    for (const line of cOut.split("\n")) {
      const t = line.trim();
      if (t.startsWith("{") && t.includes("gas_used")) { try { r = JSON.parse(t); } catch { /* skip */ } }
    }
    if (!r || r.code !== 0) failed = true;
    parts.push({
      chunk: ci, packets: chunks[ci].length,
      txhash: r && r.txhash,
      gasUsed: r && parseInt(r.gas_used, 10),
      txBytes: r && r.tx_bytes,
      height: r && parseInt(r.height, 10),
      code: r ? r.code : null,
    });
    process.stdout.write(cOut.split("\n").filter(Boolean)
      .map((l) => `  [recv${chunks.length > 1 ? " c" + ci : ""}] ${l}`).join("\n") + "\n");
  }
  const txSeconds = (Date.now() - t_tx0) / 1000;
  const creditedWallTs = Date.now() / 1000;

  const ok = !failed && parts.length === chunks.length && parts.every((p) => p.code === 0);
  // Gas and bytes are summed across the transactions the batch was split into;
  // the batch is credited when its LAST transaction lands.
  const gasUsedTotal = parts.reduce((a, p) => a + (p.gasUsed || 0), 0);
  const txBytesTotal = parts.reduce((a, p) => a + (p.txBytes || 0), 0);
  const lastHeight = parts.length ? parts[parts.length - 1].height : null;

  // The batch is credited in the block carrying its last delivery transaction,
  // so the credit time is that block's timestamp — an exact instant, not this
  // process's wall clock after a polling subprocess returned.
  let creditedTs = null, creditedHeight = null;
  if (ok && lastHeight) {
    creditedHeight = lastHeight;
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
    sizeLimit: limit, sizeLimitBinding: binding,
    chunks: chunks.length, chunkSize: perChunk, parts,
    txSeconds, creditedTs, creditedHeight, creditedWallTs,
    txhash: parts.length ? parts[parts.length - 1].txhash : null,
    gasUsed: gasUsedTotal,
    txBytes: txBytesTotal,
    code: parts.length ? parts[parts.length - 1].code : null,
    rawLog: failed ? out.split("\n").filter(Boolean).slice(-6).join(" | ").slice(0, 400) : undefined,
    consensusStatesHeld: have.length,
  };

  const sumFile = path.join(outDir, `recv-${label}.json`);
  fs.writeFileSync(sumFile, JSON.stringify(summary, null, 2));

  // Every number downstream depends on these having actually parsed out of the
  // tx result. A missing one used to read as 0 and propagate as a plausible
  // measurement, so fail here instead. The summary is already on disk at this
  // point, so a failure leaves the evidence behind rather than swallowing it.
  if (ok) {
    for (const k of ["gasUsed", "txBytes"]) {
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
    `over ${chunks.length} transaction(s) ` +
    `update_gas=${updateGas} (${updateDid ? "1 update" : "no update"}) ` +
    `signer=${summary.signerKey}/${signerAlgorithm}`);
  console.log(`  wrote ${sumFile}`);
  if (!ok) process.exit(1);
})().catch((e) => { console.error(e.message || e); process.exit(1); });

// Redemption, step 2 (Cosmos side): prove the EVM packet commitment with the
// real Ethereum light client (08-wasm-1) and submit MsgRecvPacket, which
// unescrows the originally-locked stake back to the user.
//
// Pass --replay to submit the identical proof a second time and observe IBC's
// packet-receipt replay protection.
const fs = require("fs");
const { execFileSync } = require("child_process");
const { loadEnv, evm, ethers } = require("./lib/lib");
const P = require("./lib/packet");

const REPLAY = process.argv.includes("--replay");

const beacon = (env) => env.BEACON_URL;
const gethRpc = (env) => env.GETH_RPC;
const validator = (env) => env.VALIDATOR;
const b64 = (hex) => Buffer.from(hex.replace(/^0x/, ""), "hex").toString("base64");
const get = (url) => JSON.parse(execFileSync("curl", ["-s", url], { maxBuffer: 64e6 }));

const cli = (env, args) =>
  execFileSync(env.PQCHAIND_BIN,
    [...args, "--home", env.CHAIN_HOME, "--node", env.CHAIN_NODE, "-o", "json"],
    { encoding: "utf8", maxBuffer: 64e6 });

(async () => {
  const env = loadEnv();
  const { router } = evm(env);
  const send = JSON.parse(fs.readFileSync(env.file("redeem-send.json")));
  const seq = BigInt(send.sequence);

  // Storage slot of the packet commitment inside ICS26Router.
  const ibcPath = P.packetCommitmentKey(send.sourceClient, seq);
  const slotHex = ethers.zeroPadValue(env.IBC_COMMITMENT_SLOT, 32);
  const storageKey = ethers.keccak256(ethers.concat([ethers.keccak256(ibcPath), slotHex]));
  const onchain = await router.getCommitment(ethers.keccak256(ibcPath));
  if (onchain !== send.commitment) throw new Error("commitment drifted on EVM");
  console.log(`packet commitment: ${send.commitment} (matches EVM state)`);
  console.log(`storage key      : ${storageKey}`);

  // Wait for a finalized slot whose execution block contains the send.
  const sendBlock = Number(send.sendBlockNumber);
  let hdr;
  for (let i = 0; i < 60; i++) {
    const fin = get(`${beacon(env)}/eth/v1/beacon/light_client/finality_update`).data;
    const execNum = Number(fin.finalized_header.execution.block_number);
    if (execNum >= sendBlock) { hdr = fin.finalized_header; break; }
    console.log(`  waiting for finality to cover block ${sendBlock} (at ${execNum})`);
    execFileSync("sleep", ["12"]);
  }
  if (!hdr) throw new Error("finality never advanced past the send block");

  // Make sure the light client holds a consensus state at that slot.
  const [updCmd, ...updArgs] = env.UPDATE_CLIENT_CMD.split(/\s+/);
  execFileSync(updCmd, [...updArgs, env.COSMOS_CLIENT_ID], { stdio: "inherit" });
  const states = JSON.parse(cli(env, ["query", "ibc", "client", "consensus-states", env.COSMOS_CLIENT_ID]));
  const have = states.consensus_states.map((e) => Number(e.height.revision_height));
  const proofSlot = Number(hdr.beacon.slot);
  const useSlot = have.includes(proofSlot)
    ? proofSlot
    : Math.min(...have.filter((s) => s >= proofSlot));
  if (!Number.isFinite(useSlot)) throw new Error(`no consensus state >= slot ${proofSlot}`);
  const blk = get(`${beacon(env)}/eth/v2/beacon/blocks/${useSlot}`);
  const useBlock = Number(blk.data.message.body.execution_payload.block_number);
  console.log(`proving at       : slot ${useSlot}, execution block ${useBlock}`);

  // eth_getProof via curl (ethers' socket goes stale across the finality wait).
  const proof = JSON.parse(execFileSync("curl", [
    "-s", "-m", "60", "-X", "POST", "-H", "Content-Type: application/json",
    "--data", JSON.stringify({
      jsonrpc: "2.0", method: "eth_getProof",
      params: [env.ICS26_ROUTER, [storageKey], "0x" + useBlock.toString(16)], id: 1,
    }),
    `http://${gethRpc(env)}`,
  ], { maxBuffer: 64e6 })).result;
  const sp = proof.storageProof[0];
  console.log(`storage value    : ${sp.value}`);

  const membershipProof = {
    account_proof: { storage_root: proof.storageHash, proof: proof.accountProof },
    storage_proof: {
      key: ethers.zeroPadValue(sp.key, 32), value: sp.value, proof: sp.proof,
    },
  };

  const msg = {
    "@type": "/ibc.core.channel.v2.MsgRecvPacket",
    packet: {
      sequence: send.sequence,
      source_client: send.sourceClient,
      destination_client: send.destClient,
      timeout_timestamp: send.timeoutTimestamp,
      payloads: [{
        source_port: send.payload.sourcePort,
        destination_port: send.payload.destPort,
        version: send.payload.version,
        encoding: send.payload.encoding,
        value: b64(send.payload.value),
      }],
    },
    proof_commitment: Buffer.from(JSON.stringify(membershipProof)).toString("base64"),
    proof_height: { revision_number: "0", revision_height: String(useSlot) },
    signer: validator(env),
  };
  fs.writeFileSync(env.file("msg-redeem-recv.json"), JSON.stringify(msg, null, 2));

  const [txCmd, ...txArgs] = env.SENDTX_CMD.split(/\s+/);
  const out = execFileSync(txCmd,
    [...txArgs, env.file("msg-redeem-recv.json"), env.RELAYER_KEY, "3000000"],
    { encoding: "utf8" });
  console.log(out.trim());
  if (!REPLAY) fs.writeFileSync(env.file("redeem-recv.json"), JSON.stringify({ useSlot }, null, 2));
})();

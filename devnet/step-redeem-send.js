// Redemption, step 1 (EVM side): burn the IBCERC20 voucher and emit an IBC
// packet back to Cosmos. ICS20Transfer detects that the denom carries this
// client's prefix ("returning to source") and burns rather than escrows.
const fs = require("fs");
const { loadEnv, evm, sendTx, ethers } = require("./lib/lib");
const P = require("./lib/packet");

const AMOUNT = BigInt(process.argv[2] || "2000000");

(async () => {
  const env = loadEnv();
  const { provider, router } = evm(env);
  const holder = new ethers.Wallet(env.RECEIVER_PK, provider);

  const erc20 = new ethers.Contract(env.IBCERC20, require("./abi/IBCERC20.json"), holder);
  const transfer = new ethers.Contract(env.ICS20_TRANSFER, require("./abi/ICS20Transfer.json"), holder);

  const cosmosReceiver = env.USER;
  if (!cosmosReceiver) throw new Error("USER (cosmos receiver) not set; see devnet.env.example");
  console.log(`voucher balance before: ${await erc20.balanceOf(holder.address)}`);
  console.log(`total supply before   : ${await erc20.totalSupply()}`);

  // ICS20Transfer pulls the tokens into escrow before burning them.
  let r = await sendTx(erc20, "approve", [env.ICS20_TRANSFER, AMOUNT]);
  console.log(`approve -> status ${r.status}, gas ${r.gasUsed}`);

  const timeout = BigInt(Math.floor(Date.now() / 1000) + 3600);
  const msg = {
    denom: env.IBCERC20,
    amount: AMOUNT,
    receiver: cosmosReceiver,
    sourceClient: env.ETH_CLIENT_ID,
    destPort: "transfer",
    timeoutTimestamp: timeout,
    memo: "",
  };
  r = await sendTx(transfer, "sendTransfer", [msg], { gasLimit: 3_000_000 });
  console.log(`sendTransfer -> status ${r.status}, gas ${r.gasUsed}`);

  // Pull the packet out of the router's SendPacket event.
  let packet = null;
  for (const log of r.logs) {
    try {
      const parsed = router.interface.parseLog(log);
      if (parsed && parsed.name === "SendPacket") packet = parsed.args.packet;
    } catch { /* not a router event */ }
  }
  if (!packet) throw new Error("no SendPacket event emitted");

  const pkt = {
    sequence: BigInt(packet.sequence),
    sourceClient: packet.sourceClient,
    destClient: packet.destClient,
    timeoutTimestamp: BigInt(packet.timeoutTimestamp),
    payloads: packet.payloads.map((p) => ({
      sourcePort: p.sourcePort, destPort: p.destPort,
      version: p.version, encoding: p.encoding, value: p.value,
    })),
  };
  console.log(`packet: seq=${pkt.sequence} ${pkt.sourceClient} -> ${pkt.destClient}`);
  console.log(`payload encoding: ${pkt.payloads[0].encoding}`);

  // Cross-check our commitment against what the router actually stored.
  const commitment = P.packetCommitment(pkt);
  const key = ethers.keccak256(P.packetCommitmentKey(pkt.sourceClient, pkt.sequence));
  const stored = await router.getCommitment(key);
  if (stored !== commitment) throw new Error(`commitment mismatch: ${commitment} vs ${stored}`);
  console.log(`commitment: ${commitment} (matches EVM state)`);

  console.log(`voucher balance after : ${await erc20.balanceOf(holder.address)}`);
  console.log(`total supply after    : ${await erc20.totalSupply()} (burned)`);

  fs.writeFileSync(env.file("redeem-send.json"), JSON.stringify({
    sequence: String(pkt.sequence),
    sourceClient: pkt.sourceClient,
    destClient: pkt.destClient,
    timeoutTimestamp: String(pkt.timeoutTimestamp),
    payload: {
      sourcePort: pkt.payloads[0].sourcePort,
      destPort: pkt.payloads[0].destPort,
      version: pkt.payloads[0].version,
      encoding: pkt.payloads[0].encoding,
      value: pkt.payloads[0].value,
    },
    commitment,
    sendBlockNumber: String(r.blockNumber),
    approveGas: String(r.gasUsed),
    sendGas: String(r.gasUsed),
  }, null, 2));
})();

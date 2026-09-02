// Ethereum-native leg, step 1 (EVM side): escrow a native ERC-20
// (TestERC20, devnet/deploy/TestERC20.sol) via ICS20Transfer.sendTransfer
// and emit an IBC packet to Cosmos.
//
// This is the mirror of the stake flow's Cosmos-side escrow, run from the
// EVM side instead. TEST_ERC20 is never registered as an IBCERC20 voucher
// (no setCustomERC20, nothing else maps it), so
// ICS20Transfer._sendTransferFromEscrowWithSender finds no entry in
// _ibcERC20Denoms[TEST_ERC20], takes the native branch, and escrows the
// tokens rather than burning them. See docs/architecture.md and the
// ICS20Transfer.sol excerpt discussed alongside devnet/deploy/TestERC20.sol.
//
// Usage: node step-native-send.js [amount]
const fs = require("fs");
const { loadEnv, evm, sendTx, ethers } = require("./lib/lib");
const P = require("./lib/packet");

const AMOUNT = BigInt(process.argv[2] || "2000000");

(async () => {
  const env = loadEnv();
  const { provider, router } = evm(env);
  const holder = new ethers.Wallet(env.RECEIVER_PK, provider);

  const testErc20 = new ethers.Contract(env.TEST_ERC20, require("./abi/TestERC20.json"), holder);
  const transfer = new ethers.Contract(env.ICS20_TRANSFER, require("./abi/ICS20Transfer.json"), holder);

  const cosmosReceiver = env.USER;
  if (!cosmosReceiver) throw new Error("USER (cosmos receiver) not set; see devnet.env.example");

  // TestERC20.mint is unrestricted, so the holder tops itself up directly
  // rather than routing a mint through the deployer key.
  const balBefore = await testErc20.balanceOf(holder.address);
  if (balBefore < AMOUNT) {
    const need = AMOUNT - balBefore;
    const m = await sendTx(testErc20, "mint", [holder.address, need]);
    console.log(`mint ${need} -> status ${m.status}, gas ${m.gasUsed}`);
  }
  console.log(`token balance before: ${await testErc20.balanceOf(holder.address)}`);
  console.log(`total supply before : ${await testErc20.totalSupply()}`);

  // ICS20Transfer pulls the tokens into escrow (native branch: no burn,
  // because TEST_ERC20 is not a mapped IBCERC20 voucher).
  let r = await sendTx(testErc20, "approve", [env.ICS20_TRANSFER, AMOUNT]);
  console.log(`approve -> status ${r.status}, gas ${r.gasUsed}`);

  const timeout = BigInt(Math.floor(Date.now() / 1000) + 3600);
  const msg = {
    denom: env.TEST_ERC20,
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
  console.log(`payload denom (raw, pre-prefix): ${ethers.AbiCoder.defaultAbiCoder()
    .decode(["(string,string,string,uint256,string)"], pkt.payloads[0].value)[0][0]}`);

  // Cross-check our commitment against what the router actually stored.
  const commitment = P.packetCommitment(pkt);
  const key = ethers.keccak256(P.packetCommitmentKey(pkt.sourceClient, pkt.sequence));
  const stored = await router.getCommitment(key);
  if (stored !== commitment) throw new Error(`commitment mismatch: ${commitment} vs ${stored}`);
  console.log(`commitment: ${commitment} (matches EVM state)`);

  console.log(`token balance after : ${await testErc20.balanceOf(holder.address)} (escrowed, not burned)`);
  console.log(`total supply after  : ${await testErc20.totalSupply()} (unchanged — escrow, not mint/burn)`);

  fs.writeFileSync(env.file("native-send.json"), JSON.stringify({
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
  console.log(`wrote ${env.file("native-send.json")}`);
})();

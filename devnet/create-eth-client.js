// Create (or reuse) the EVM-side SP1ICS07Tendermint light client and pair it
// with a Cosmos-side cw-ics08-wasm-eth client, registering the counterparty
// on both sides. bring-up-devnet.sh does not do this (see
// devnet/deploy/README.md); this is the missing step, matching
// solidity-ibc-eureka/e2e/interchaintestv8/ibc_eureka_test.go's
// "Deploy SP1 ICS07 contract" / "Add client and counterparty on EVM" /
// "Register counterparty on Cosmos chain" subtests.
//
// Idempotent: the EVM and Cosmos sides are checked and completed
// independently, so a partial prior run (e.g. EVM client created but Cosmos
// registration failed) resumes correctly rather than reporting false success.
//
// Which SP1 verifier the new client is bound to (SP1MockVerifier, near-free
// but not a real proof; or SP1VerifierGroth16, a real ~10 min proof — see
// devnet/README.md#proving) is an EXPLICIT, REQUIRED argument, not a default,
// so a client's verifier is always a deliberate choice made visible in the
// log, not something left over from whichever value deploy.env happened to
// hold. A client's verifier is fixed permanently at creation and can never be
// switched later (see devnet/deploy/README.md#verifier-paths).
//
// Usage: node create-eth-client.js --verifier=mock|real
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");
const { loadEnv, sendTx, sendRawTx, ethers, abi, config } = require("./lib/lib");
const proofapi = require("./lib/proofapi");

function parseVerifierArg(argv) {
  const arg = argv.find((a) => a.startsWith("--verifier="));
  const mode = arg && arg.split("=")[1];
  if (mode !== "mock" && mode !== "real") {
    console.error(
      "usage: node create-eth-client.js --verifier=mock|real\n\n" +
      "  mock  bind the new client to SP1MockVerifier (near-instant, NOT a real\n" +
      "        proof — for mechanism/scaling tests, e.g. experiments/batch_scaling)\n" +
      "  real  bind the new client to SP1VerifierGroth16 (real proving, ~10 min/proof\n" +
      "        — see devnet/README.md#proving)\n\n" +
      "No default: which verifier a client is bound to is permanent and affects every\n" +
      "result produced against it, so it must be chosen explicitly every time."
    );
    process.exit(2);
  }
  return mode;
}

// Avoids lib.js's evm(): that also builds an SP1ICS07Tendermint handle from
// env.SP1_ICS07, which may not exist yet.
function evmNoLc(env) {
  const url = env.GETH_RPC.startsWith("http") ? env.GETH_RPC : `http://${env.GETH_RPC}`;
  const provider = new ethers.JsonRpcProvider(url, undefined, { cacheTimeout: -1 });
  const deployer = new ethers.Wallet(env.DEPLOYER_PK, provider);
  return {
    provider, deployer,
    router: new ethers.Contract(env.ICS26_ROUTER, abi("ICS26Router"), deployer),
  };
}

function upsertDeployEnv(env, updates) {
  const file = env.file("deploy.env");
  let lines = fs.existsSync(file) ? fs.readFileSync(file, "utf8").split("\n") : [];
  for (const [key, value] of Object.entries(updates)) {
    lines = lines.filter((l) => !l.startsWith(`${key}=`));
    lines.push(`${key}=${value}`);
  }
  fs.writeFileSync(file, lines.filter((l) => l.length > 0).join("\n") + "\n");
}

async function hasLiveCode(provider, address) {
  if (!address) return false;
  const code = await provider.getCode(address).catch(() => "0x");
  return code !== "0x";
}

// keccak256("VERIFIER()")[:4] — the SP1ICS07Tendermint accessor for the
// verifier address it was constructed with (immutable, so this is a live
// read of a permanent fact, not just of deploy.env's bookkeeping).
const VERIFIER_SELECTOR = "0x08c84e70";
async function boundVerifier(provider, sp1Ics07Address) {
  const result = await provider.call({ to: sp1Ics07Address, data: VERIFIER_SELECTOR });
  return "0x" + result.slice(-40);
}

// pqchaind prints full CLI usage to stderr on a "not found" query error;
// suppress it since that's an expected outcome here, not a failure to report.
function counterpartyOf(env, clientId) {
  try {
    const out = execFileSync(env.PQCHAIND_BIN,
      ["query", "ibc", "client", "counterparty-info", clientId, "--home", env.CHAIN_HOME, "--node", env.CHAIN_NODE, "-o", "json"],
      { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] });
    return JSON.parse(out).counterparty_info?.client_id || null;
  } catch {
    return null;
  }
}

// Returns { clientId, address } for a live, correctly-paired EVM client, or
// deploys and registers a new one. Does not touch the Cosmos side.
async function getOrCreateEvmClient(env, { provider, router, deployer }, chainId, cosmosClientId, verifierMode, verifierAddr) {
  if (env.ETH_CLIENT_ID) {
    const clientAddr = await router.getClient(env.ETH_CLIENT_ID).catch(() => null);
    if (clientAddr && await hasLiveCode(provider, clientAddr)) {
      const counterparty = await router.getCounterparty(env.ETH_CLIENT_ID);
      if (counterparty.clientId === cosmosClientId) {
        // A client's verifier is fixed forever at creation — reusing one
        // bound to the WRONG verifier for the requested mode would silently
        // give real-proving results to a caller that asked for mock, or
        // vice versa. Confirm the live binding before reusing it.
        const bound = await boundVerifier(provider, clientAddr);
        if (bound.toLowerCase() !== verifierAddr.toLowerCase()) {
          throw new Error(
            `${env.ETH_CLIENT_ID} @ ${clientAddr} is already bound to ${bound}, not the ` +
            `requested --verifier=${verifierMode} verifier (${verifierAddr}). A client's ` +
            `verifier can never be changed after creation. Create a new Cosmos-side client ` +
            `(devnet/scripts/create-light-client.sh) to pair with a fresh EVM client bound to ` +
            `${verifierMode}, then re-run.`);
        }
        console.log(`reusing ${env.ETH_CLIENT_ID} @ ${clientAddr} (paired with ${cosmosClientId}, bound to ${verifierMode})`);
        return { clientId: env.ETH_CLIENT_ID, address: clientAddr };
      }
      console.log(`${env.ETH_CLIENT_ID} exists but is paired with "${counterparty.clientId}", not "${cosmosClientId}" — creating a new client`);
    } else {
      console.log(`${env.ETH_CLIENT_ID} has no live code on this chain — creating a new client`);
    }
  }

  // A Cosmos client's counterparty is set once and is immutable. addClient's
  // 2-arg overload always assigns "client-<nextClientSeq>", so the ID a
  // fresh deploy gets is predictable ahead of time — check the pairing is
  // even possible before spending gas on a deploy that can't be registered.
  const existingCounterparty = counterpartyOf(env, cosmosClientId);
  if (existingCounterparty) {
    const nextSeq = await router.getNextClientSeq();
    const predictedClientId = `client-${nextSeq}`;
    if (existingCounterparty !== predictedClientId) {
      throw new Error(
        `${cosmosClientId} is immutably paired with "${existingCounterparty}", but a fresh deploy would be ` +
        `assigned "${predictedClientId}" — they can never match. Create a new Cosmos client instead ` +
        `(devnet/scripts/create-light-client.sh) and re-run.`
      );
    }
  }

  console.log(`cosmos: ${env.CHAIN_ID} (${cosmosClientId})  eth: ${chainId}`);
  console.log(`sp1 verifier: ${verifierAddr} (--verifier=${verifierMode})  role_manager: ${env.ICS26_ROUTER}`);

  const client = proofapi.connect(env);
  console.log("requesting SP1ICS07Tendermint deployment tx from proof-api...");
  const created = await proofapi.createClient(client, {
    srcChain: env.CHAIN_ID,
    dstChain: chainId,
    parameters: { sp1_verifier: verifierAddr, role_manager: env.ICS26_ROUTER },
  });

  const data = "0x" + Buffer.from(created.tx).toString("hex");
  const r = await sendRawTx(deployer, null, data, { gasLimit: 15_000_000 });
  console.log(`deploy -> status ${r.status}, gas ${r.gasUsed}, block ${r.blockNumber}`);
  if (r.status !== 1) throw new Error("SP1ICS07Tendermint deployment tx reverted");
  const sp1Ics07Address = r.contractAddress;
  if (!sp1Ics07Address) throw new Error("no contractAddress in deployment receipt");
  console.log(`SP1ICS07Tendermint deployed at: ${sp1Ics07Address}`);

  const actuallyBound = await boundVerifier(provider, sp1Ics07Address);
  if (actuallyBound.toLowerCase() !== verifierAddr.toLowerCase()) {
    throw new Error(
      `deployed contract's VERIFIER() = ${actuallyBound}, expected ${verifierAddr} ` +
      `(--verifier=${verifierMode}) — proof-api built the deployment tx with the wrong parameter.`);
  }
  console.log(`confirmed on-chain: VERIFIER() = ${actuallyBound} (--verifier=${verifierMode})`);

  // addClient(counterpartyInfo, client): 2-arg, permissionless, auto-ID overload.
  const counterpartyInfo = [cosmosClientId, [ethers.toUtf8Bytes("ibc"), ethers.toUtf8Bytes("")]];
  const addR = await sendTx(router, "addClient((string,bytes[]),address)", [counterpartyInfo, sp1Ics07Address], { gasLimit: 1_000_000 });
  console.log(`addClient -> status ${addR.status}, gas ${addR.gasUsed}`);

  let ethClientId = null;
  for (const log of addR.logs) {
    try {
      const parsed = router.interface.parseLog(log);
      if (parsed && parsed.name === "ICS02ClientAdded") ethClientId = parsed.args.clientId;
    } catch { /* not a router event */ }
  }
  if (!ethClientId) throw new Error("no ICS02ClientAdded event emitted");
  if (existingCounterparty && existingCounterparty !== ethClientId) {
    throw new Error(`${cosmosClientId}'s counterparty ("${existingCounterparty}") no longer matches the ` +
      `just-created "${ethClientId}" — the router's sequence must have advanced mid-run. Re-run this script.`);
  }

  const registered = await router.getClient(ethClientId);
  if (registered.toLowerCase() !== sp1Ics07Address.toLowerCase()) {
    throw new Error(`getClient(${ethClientId}) = ${registered}, expected ${sp1Ics07Address}`);
  }
  const counterparty = await router.getCounterparty(ethClientId);
  if (counterparty.clientId !== cosmosClientId) {
    throw new Error(`getCounterparty(${ethClientId}).clientId = ${counterparty.clientId}, expected ${cosmosClientId}`);
  }
  console.log(`EVM verified: ${ethClientId} -> ${sp1Ics07Address}, counterparty ${cosmosClientId}`);

  upsertDeployEnv(env, { ETH_CLIENT_ID: ethClientId, SP1_ICS07: sp1Ics07Address });
  console.log(`wrote ETH_CLIENT_ID=${ethClientId} SP1_ICS07=${sp1Ics07Address} to ${env.file("deploy.env")}`);
  return { clientId: ethClientId, address: sp1Ics07Address };
}

function registerCosmosCounterparty(env, cosmosClientId, ethClientId) {
  const existing = counterpartyOf(env, cosmosClientId);
  if (existing === ethClientId) {
    console.log(`Cosmos side already registered: ${cosmosClientId} -> ${ethClientId}`);
    return;
  }
  if (existing) {
    throw new Error(`${cosmosClientId} is immutably paired with "${existing}", not "${ethClientId}".`);
  }

  // "keys show" doesn't accept --node, unlike query/tx.
  const validator = execFileSync(env.PQCHAIND_BIN,
    ["keys", "show", env.RELAYER_KEY || "validator", "-a", "--home", env.CHAIN_HOME, "--keyring-backend", "test"],
    { encoding: "utf8" }).trim();
  const msgPath = path.join(env.DEVNET_DIR, `msg-register-counterparty-${cosmosClientId}.json`);
  fs.writeFileSync(msgPath, JSON.stringify({
    "@type": "/ibc.core.client.v2.MsgRegisterCounterparty",
    client_id: cosmosClientId,
    counterparty_merkle_prefix: [""],
    counterparty_client_id: ethClientId,
    signer: validator,
  }, null, 2));
  const [txCmd, ...txArgs] = env.SENDTX_CMD.split(/\s+/);
  const out = execFileSync(txCmd, [...txArgs, msgPath, env.RELAYER_KEY || "validator", "300000"], { encoding: "utf8" });
  fs.unlinkSync(msgPath);
  const result = JSON.parse(out.trim().split("\n").pop());
  if (result.code !== 0) throw new Error(`MsgRegisterCounterparty failed (code ${result.code}): ${result.raw_log || ""}`);
  console.log(`MsgRegisterCounterparty -> code 0, height ${result.height}, gas ${result.gas_used}`);

  if (counterpartyOf(env, cosmosClientId) !== ethClientId) {
    throw new Error(`Cosmos-side verification failed: counterparty-info(${cosmosClientId}) != ${ethClientId}`);
  }
  console.log(`Cosmos verified: ${cosmosClientId} -> ${ethClientId}`);
}

(async () => {
  const verifierMode = parseVerifierArg(process.argv.slice(2));
  const env = loadEnv();
  config.require_(env, "CHAIN_ID", "PROOF_API_ADDR", "ICS26_ROUTER", "DEPLOYER_PK", "COSMOS_CLIENT_ID",
    "SP1_VERIFIER_MOCK", "SP1_VERIFIER_GROTH16");
  const verifierAddr = verifierMode === "mock" ? env.SP1_VERIFIER_MOCK : env.SP1_VERIFIER_GROTH16;

  console.log("=".repeat(72));
  console.log(`VERIFIER MODE: ${verifierMode.toUpperCase()} (${verifierAddr})`);
  console.log(verifierMode === "mock"
    ? "  near-instant, NOT a real proof — every result produced against the"
    : "  REAL Groth16 proving, ~10 min/proof — see devnet/README.md#proving");
  console.log("=".repeat(72));

  const evm = evmNoLc(env);
  const chainId = (await evm.provider.getNetwork()).chainId.toString();
  const cosmosClientId = env.COSMOS_CLIENT_ID;

  const { clientId: ethClientId } = await getOrCreateEvmClient(env, evm, chainId, cosmosClientId, verifierMode, verifierAddr);
  registerCosmosCounterparty(env, cosmosClientId, ethClientId);
  console.log(`done — ${ethClientId} is bound to ${verifierMode.toUpperCase()} (${verifierAddr})`);
})().catch((e) => {
  console.error(e.message || e);
  process.exit(1);
});

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
// Usage: node create-eth-client.js
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");
const { loadEnv, sendTx, sendRawTx, ethers, abi, config } = require("./lib/lib");
const proofapi = require("./lib/proofapi");

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
async function getOrCreateEvmClient(env, { provider, router, deployer }, chainId, cosmosClientId) {
  if (env.ETH_CLIENT_ID) {
    const clientAddr = await router.getClient(env.ETH_CLIENT_ID).catch(() => null);
    if (clientAddr && await hasLiveCode(provider, clientAddr)) {
      const counterparty = await router.getCounterparty(env.ETH_CLIENT_ID);
      if (counterparty.clientId === cosmosClientId) {
        console.log(`reusing ${env.ETH_CLIENT_ID} @ ${clientAddr} (paired with ${cosmosClientId})`);
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
  console.log(`sp1 verifier: ${env.SP1_VERIFIER_GROTH16}  role_manager: ${env.ICS26_ROUTER}`);

  const client = proofapi.connect(env);
  console.log("requesting SP1ICS07Tendermint deployment tx from proof-api...");
  const created = await proofapi.createClient(client, {
    srcChain: env.CHAIN_ID,
    dstChain: chainId,
    parameters: { sp1_verifier: env.SP1_VERIFIER_GROTH16, role_manager: env.ICS26_ROUTER },
  });

  const data = "0x" + Buffer.from(created.tx).toString("hex");
  const r = await sendRawTx(deployer, null, data, { gasLimit: 15_000_000 });
  console.log(`deploy -> status ${r.status}, gas ${r.gasUsed}, block ${r.blockNumber}`);
  if (r.status !== 1) throw new Error("SP1ICS07Tendermint deployment tx reverted");
  const sp1Ics07Address = r.contractAddress;
  if (!sp1Ics07Address) throw new Error("no contractAddress in deployment receipt");
  console.log(`SP1ICS07Tendermint deployed at: ${sp1Ics07Address}`);

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
  const env = loadEnv();
  config.require_(env, "CHAIN_ID", "PROOF_API_ADDR", "ICS26_ROUTER", "SP1_VERIFIER_GROTH16", "DEPLOYER_PK", "COSMOS_CLIENT_ID");
  const evm = evmNoLc(env);
  const chainId = (await evm.provider.getNetwork()).chainId.toString();
  const cosmosClientId = env.COSMOS_CLIENT_ID;

  const { clientId: ethClientId } = await getOrCreateEvmClient(env, evm, chainId, cosmosClientId);
  registerCosmosCounterparty(env, cosmosClientId, ethClientId);
})();

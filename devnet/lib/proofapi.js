// Minimal gRPC client for solidity-ibc-eureka's proof-api.
//
// The devnet used to hand-encode SP1 proof structs with an empty proof body,
// which only ever worked because the EVM light client ran SP1MockVerifier.
// With the real Groth16 verifier bound, proofs must come from a prover, so the
// relay tx is requested from proof-api instead of assembled here.
//
// proof-api is expected to already be running against this devnet; see
// devnet/README.md. PROOF_API_ADDR and PROOF_API_PROTO come from devnet.env.
const grpc = require("@grpc/grpc-js");
const protoLoader = require("@grpc/proto-loader");

function loadService(protoPath) {
  const def = protoLoader.loadSync(protoPath, {
    keepCase: true,
    longs: String,
    enums: String,
    defaults: true,
    oneofs: true,
  });
  return grpc.loadPackageDefinition(def).proofapi.ProofApiService;
}

// Proof generation is minutes, not seconds: a single Groth16 proof on CPU runs
// ~8-9 minutes on this devnet. gRPC's default deadline would abort long before.
const DEFAULT_TIMEOUT_MS = 30 * 60 * 1000;

function connect(env) {
  const addr = env.PROOF_API_ADDR;
  const proto = env.PROOF_API_PROTO;
  if (!addr) throw new Error("PROOF_API_ADDR not set (see devnet.env.example)");
  if (!proto) throw new Error("PROOF_API_PROTO not set (see devnet.env.example)");
  const Service = loadService(proto);
  return new Service(addr, grpc.credentials.createInsecure(), {
    // Relay responses carry a full Groth16 proof; the 4MB default is ample but
    // be explicit rather than rely on it.
    "grpc.max_receive_message_length": 32 * 1024 * 1024,
  });
}

function call(client, method, req, timeoutMs = DEFAULT_TIMEOUT_MS) {
  const deadline = new Date(Date.now() + timeoutMs);
  return new Promise((resolve, reject) => {
    client[method](req, { deadline }, (err, resp) => (err ? reject(err) : resolve(resp)));
  });
}

// Ask proof-api for the multicall relay tx that delivers `sourceTxIds` from the
// Cosmos chain to the EVM ICS26Router. The returned tx already contains the
// client update and the packet message, each carrying a real SP1 proof.
async function relayByTx(client, { srcChain, dstChain, sourceTxIds, srcClientId, dstClientId }) {
  const resp = await call(client, "RelayByTx", {
    src_chain: srcChain,
    dst_chain: dstChain,
    source_tx_ids: sourceTxIds,
    timeout_tx_ids: [],
    src_client_id: srcClientId,
    dst_client_id: dstClientId,
  });
  if (!resp.tx || resp.tx.length === 0) throw new Error("proof-api returned an empty relay tx");
  return { tx: resp.tx, address: resp.address };
}

async function info(client, { srcChain, dstChain }) {
  return call(client, "Info", { src_chain: srcChain, dst_chain: dstChain }, 30_000);
}

module.exports = { connect, relayByTx, info };

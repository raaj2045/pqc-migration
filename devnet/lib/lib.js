// Shared helpers for the devnet scripts: config, providers, ABIs, and the
// Cosmos RPC/CLI glue. All host paths and endpoints come from config.js
// (devnet.env / environment / the devnet's generated env files).
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");
const { ethers } = require("ethers");
const config = require("./config");

const ROOT = path.resolve(__dirname, "..");

// Kept as `loadEnv` so callers read naturally; returns the resolved config.
const loadEnv = () => config.load();

const abi = (name) => JSON.parse(fs.readFileSync(path.join(ROOT, "abi", `${name}.json`), "utf8"));

// Hardhat automines and refuses queued txs, so each send needs its own
// nonce. ethers caches eth_getTransactionCount for ~250ms, which hands two
// back-to-back sends the same value, so track it locally instead.
const nonces = new Map();
async function sendTx(contract, method, args, overrides = {}) {
  const from = await contract.runner.getAddress();
  if (!nonces.has(from)) {
    nonces.set(from, await contract.runner.provider.getTransactionCount(from, "pending"));
  }
  const nonce = nonces.get(from);
  nonces.set(from, nonce + 1);
  const tx = await contract[method](...args, { ...overrides, nonce });
  return tx.wait();
}

function evm(env) {
  config.require_(env, "GETH_RPC", "DEPLOYER_PK", "ICS26_ROUTER");
  const url = env.GETH_RPC.startsWith("http") ? env.GETH_RPC : `http://${env.GETH_RPC}`;
  // cacheTimeout -1 disables ethers' internal RPC response cache.
  const provider = new ethers.JsonRpcProvider(url, undefined, { cacheTimeout: -1 });
  const deployer = new ethers.Wallet(env.DEPLOYER_PK, provider);
  return {
    provider,
    deployer,
    router: new ethers.Contract(env.ICS26_ROUTER, abi("ICS26Router"), deployer),
    transfer: new ethers.Contract(env.ICS20_TRANSFER, abi("ICS20Transfer"), deployer),
    lc: new ethers.Contract(env.SP1_ICS07, abi("SP1ICS07Tendermint"), deployer),
  };
}

// Cosmos node RPC as an http:// URL, derived from CHAIN_NODE (tcp://host:port).
function cosmosRpcUrl() {
  const cfg = config.load();
  return cfg.CHAIN_NODE.replace(/^tcp:/, "http:");
}

function cosmosCli(args) {
  const cfg = config.require_(config.load(), "PQCHAIND_BIN", "CHAIN_HOME", "CHAIN_NODE");
  return execFileSync(cfg.PQCHAIND_BIN,
    [...args, "--home", cfg.CHAIN_HOME, "--node", cfg.CHAIN_NODE], {
      encoding: "utf8",
      maxBuffer: 64 * 1024 * 1024,
    });
}

async function cosmosRpc(pathAndQuery) {
  const r = await fetch(`${cosmosRpcUrl()}${pathAndQuery}`);
  return r.json();
}

// Header fields at a given height, with the timestamp converted to unix nanos.
async function cosmosHeader(height) {
  const j = await cosmosRpc(`/block?height=${height}`);
  const h = j.result.block.header;
  const [sec, frac = "0"] = h.time.replace("Z", "").split(".");
  const nanos = BigInt(Math.floor(new Date(sec + "Z").getTime() / 1000)) * 1000000000n
    + BigInt((frac + "000000000").slice(0, 9));
  return {
    height: BigInt(h.height),
    appHash: "0x" + h.app_hash,
    nextValidatorsHash: "0x" + h.next_validators_hash,
    timestampNanos: nanos,
  };
}

async function cosmosHeight() {
  const j = await cosmosRpc("/status");
  return Number(j.result.sync_info.latest_block_height);
}

async function waitCosmosHeight(target) {
  for (let i = 0; i < 60; i++) {
    if ((await cosmosHeight()) >= target) return;
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error(`cosmos did not reach height ${target}`);
}

// ABI coder shapes mirroring the Solidity structs.
const coder = ethers.AbiCoder.defaultAbiCoder();
const T_CLIENT_STATE = "(string,(uint8,uint8),(uint64,uint64),uint32,uint32,bool,uint8)";
const T_CONSENSUS_STATE = "(uint128,bytes32,bytes32)";
const T_KVPAIR = "(bytes[],bytes)";
const T_MEMBERSHIP_OUTPUT = `(bytes32,${T_KVPAIR}[])`;
const T_SP1_PROOF = "(bytes32,bytes,bytes)";
const T_MEMBERSHIP_PROOF_INNER = `(${T_SP1_PROOF},${T_CONSENSUS_STATE})`;
const T_MEMBERSHIP_PROOF = `(uint8,bytes)`;
const T_UPDATE_OUTPUT =
  `(${T_CLIENT_STATE},${T_CONSENSUS_STATE},${T_CONSENSUS_STATE},uint128,(uint64,uint64),(uint64,uint64))`;
const T_MSG_UPDATE_CLIENT = `(${T_SP1_PROOF})`;

module.exports = {
  ROOT, config, loadEnv, abi, evm, sendTx, cosmosCli, cosmosRpc, cosmosRpcUrl,
  cosmosHeader, cosmosHeight,
  waitCosmosHeight, coder, ethers,
  T_CLIENT_STATE, T_CONSENSUS_STATE, T_KVPAIR, T_MEMBERSHIP_OUTPUT, T_SP1_PROOF,
  T_MEMBERSHIP_PROOF_INNER, T_MEMBERSHIP_PROOF, T_UPDATE_OUTPUT, T_MSG_UPDATE_CLIENT,
};

// Direct Tendermint RPC / protobuf broadcast path, replacing the simd CLI
// shell-out. The CLI path added ~400-3800 ms per tx of process-spawn and
// JSON-serialisation overhead that capped single-signer throughput at
// ~2.5 tx/s (see validator_scaling v4 smoke). This client signs MsgMint
// in-process with DirectSecp256k1Wallet and broadcasts over Tendermint RPC
// via cosmjs SigningStargateClient, bringing per-tx latency to tens of ms.
//
// MsgMint is registered manually (cosmos-sdk lockandmint is out-of-tree for
// cosmjs) with a minimal protobuf encoder — three string fields only.

const { SigningStargateClient } = require("@cosmjs/stargate");
const { DirectSecp256k1Wallet, Registry } = require("@cosmjs/proto-signing");
const { TxRaw } = require("cosmjs-types/cosmos/tx/v1beta1/tx");
const { fromHex } = require("@cosmjs/encoding");

const MSG_MINT_TYPE_URL = "/cosmos.lockandmint.v1.MsgMint";

// Hand-rolled protobuf codec for MsgMint { authority, receiver, amount }.
// Field tags: authority=1, receiver=2, amount=3 — all wire-type 2 (length-
// delimited string). cosmjs's Registry calls .encode(msg).finish() so we
// use the BufferWriter from protobufjs that comes with cosmjs-types.
const { Writer, Reader } = require("protobufjs/minimal");

const MsgMint = {
  typeUrl: MSG_MINT_TYPE_URL,
  encode(message, writer) {
    writer = writer || Writer.create();
    if (message.authority !== undefined && message.authority !== "") {
      writer.uint32(10).string(message.authority);
    }
    if (message.receiver !== undefined && message.receiver !== "") {
      writer.uint32(18).string(message.receiver);
    }
    if (message.amount !== undefined && message.amount !== "") {
      writer.uint32(26).string(message.amount);
    }
    return writer;
  },
  decode(input, length) {
    const reader = input instanceof Reader ? input : new Reader(input);
    const end = length === undefined ? reader.len : reader.pos + length;
    const message = { authority: "", receiver: "", amount: "" };
    while (reader.pos < end) {
      const tag = reader.uint32();
      switch (tag >>> 3) {
        case 1: message.authority = reader.string(); break;
        case 2: message.receiver = reader.string(); break;
        case 3: message.amount = reader.string(); break;
        default: reader.skipType(tag & 7);
      }
    }
    return message;
  },
  fromPartial(object) {
    return {
      authority: object.authority ?? "",
      receiver: object.receiver ?? "",
      amount: object.amount ?? "",
    };
  },
};

class CosmosGrpcClient {
  constructor() {
    this.wallet = null;
    this.client = null;
    this.signerAddress = null;
    this.chainId = null;
    this.registry = null;
  }

  async init({ privKeyHex, rpcUrl, chainId, addressPrefix }) {
    const prefix = addressPrefix || "cosmos";
    const privBytes = fromHex(privKeyHex.replace(/^0x/, ""));
    this.wallet = await DirectSecp256k1Wallet.fromKey(privBytes, prefix);
    const accounts = await this.wallet.getAccounts();
    this.signerAddress = accounts[0].address;

    this.registry = new Registry();
    this.registry.register(MSG_MINT_TYPE_URL, MsgMint);

    this.client = await SigningStargateClient.connectWithSigner(
      rpcUrl,
      this.wallet,
      { registry: this.registry }
    );
    this.chainId = chainId;
  }

  // Sign + broadcast a MsgMint. Uses broadcast_tx_sync semantics (returns
  // after CheckTx, not DeliverTx) for latency. Caller supplies sequence
  // and accountNumber to bypass the chain-state lookup — required because
  // cosmos-sdk reads sequence from committed state, which is the whole
  // reason we cache it locally.
  async submitMint({ authority, receiver, amount, sequence, accountNumber, fee, memo }) {
    const msgs = [{
      typeUrl: MSG_MINT_TYPE_URL,
      value: { authority, receiver, amount: String(amount) },
    }];
    const signerData = {
      accountNumber,
      sequence,
      chainId: this.chainId,
    };
    const txRaw = await this.client.sign(
      this.signerAddress,
      msgs,
      fee,
      memo || "",
      signerData
    );
    const txBytes = TxRaw.encode(txRaw).finish();
    // cosmjs StargateClient.broadcastTxSync semantics:
    //   success (CheckTx code==0) → returns the uppercase-hex tx hash string.
    //   failure (CheckTx code!=0) → throws BroadcastTxError(code, codespace, log).
    // We normalise to a uniform shape so callers don't have to know cosmjs.
    try {
      const txHash = await this.client.broadcastTxSync(txBytes);
      return { ok: true, txHash };
    } catch (e) {
      // BroadcastTxError has .code and .log; other errors (network etc.)
      // may not. Surface enough to distinguish CheckTx-reject vs RPC-failure.
      const err = new Error(`broadcast failed: ${e.message}`);
      err.cause = e;
      err.checkTxCode = e.code;
      err.log = e.log;
      throw err;
    }
  }

  disconnect() {
    if (this.client) {
      try { this.client.disconnect(); } catch (_) { /* best effort */ }
      this.client = null;
    }
  }
}

module.exports = { CosmosGrpcClient, MSG_MINT_TYPE_URL };

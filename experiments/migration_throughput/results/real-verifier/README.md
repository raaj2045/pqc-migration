# Real-verifier relay costs

Gas and calldata for bridge relays verified by the **real** `SP1VerifierGroth16`
and the real `cw-ics08-wasm-eth` BLS light client. The sweep in the parent
directory ran its forward leg against `SP1MockVerifier`; these figures cover the
verified path.

Each JSON is written by the relay script itself (`devnet/step-recv.js`,
`devnet/step-redeem-ack.js`) and records `recvGas`/`ackGas`, `relayTxBytes` and
`proveSeconds` for one packet.

## Forward leg — Cosmos → Ethereum, SP1 Groth16

| File | Case | Gas | Calldata | Proving |
|---|---|---|---|---|
| `leg1-n1-p0.json` | standalone packet | 1,125,804 | 3,620 B | 757.3 s |
| `recv-result.json` | standalone packet | 1,125,816 | 3,620 B | 617.6 s |
| `leg1-n5-p1.json` | behind an advanced client | 547,786 | 3,620 B | 622.4 s |
| `leg1-n5-p2.json` | " | 547,774 | 3,620 B | 602.7 s |
| `leg1-n5-p3.json` | " | 547,774 | 3,620 B | 601.5 s |
| `leg1-n5-p5.json` | " | 547,762 | 3,620 B | 607.2 s |

A packet costs about 578,000 gas less when a preceding relay has already
advanced the light client, at identical calldata size — the difference is
execution work in verifying and storing a consensus state, not payload.

Each packet still gets its own relay transaction and its own proof: there is no
batched relay on this leg. The four low-cost packets above were committed at
nearly the same Cosmos height, so one client update served all of them; packets
spread across different heights each require their own.

## Return leg — Ethereum → Cosmos, BLS sync committee

`redeem-ack.json` covers the EVM-side ack. The Cosmos-side costs are reported by
`sendtx.py` per transaction:

| Case | `MsgUpdateClient` | `MsgAcknowledgement` |
|---|---|---|
| Single packet | 935,395 | 271,643 |
| Four packets, one finality window | **0** (client already current) | 274,071 / 269,849 / 269,849 / 271,643 |

The update is per finality window and the acknowledgement is per packet, so a
batch of four pays no update gas at all. Over five packets sharing one window
the whole-window cost is 2,292,450 gas, or 458,490 per packet, against 1,207,038
for a single packet alone.

---

[Experiment README](../../README.md) · [Project README](../../../../README.md)

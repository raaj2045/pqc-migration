// Minimal protobuf reader for ibc channel/v2 Packet, plus the ICS24
// commitment scheme, mirrored from ICS24Host.sol.
const { ethers } = require("ethers");

function reader(buf) {
  let i = 0;
  const varint = () => {
    let x = 0n, shift = 0n;
    for (;;) {
      const b = buf[i++];
      x |= BigInt(b & 0x7f) << shift;
      if ((b & 0x80) === 0) break;
      shift += 7n;
    }
    return x;
  };
  return {
    done: () => i >= buf.length,
    varint,
    key: () => {
      const k = varint();
      return { field: Number(k >> 3n), wire: Number(k & 7n) };
    },
    bytes: () => {
      const n = Number(varint());
      const b = buf.subarray(i, i + n);
      i += n;
      return b;
    },
    skip: (wire) => {
      if (wire === 0) varint();
      else if (wire === 2) { const n = Number(varint()); i += n; }
      else if (wire === 5) i += 4;
      else if (wire === 1) i += 8;
      else throw new Error(`unsupported wire type ${wire}`);
    },
  };
}

function decodePayload(buf) {
  const r = reader(buf);
  const p = { sourcePort: "", destPort: "", version: "", encoding: "", value: "0x" };
  while (!r.done()) {
    const { field, wire } = r.key();
    if (wire !== 2) { r.skip(wire); continue; }
    const b = r.bytes();
    if (field === 1) p.sourcePort = b.toString();
    else if (field === 2) p.destPort = b.toString();
    else if (field === 3) p.version = b.toString();
    else if (field === 4) p.encoding = b.toString();
    else if (field === 5) p.value = "0x" + b.toString("hex");
  }
  return p;
}

// Decode a channel/v2 Packet from the `encoded_packet_hex` event attribute.
function decodePacket(hex) {
  const buf = Buffer.from(hex.replace(/^0x/, ""), "hex");
  const r = reader(buf);
  const pkt = { sequence: 0n, sourceClient: "", destClient: "", timeoutTimestamp: 0n, payloads: [] };
  while (!r.done()) {
    const { field, wire } = r.key();
    if (field === 1 && wire === 0) pkt.sequence = r.varint();
    else if (field === 2 && wire === 2) pkt.sourceClient = r.bytes().toString();
    else if (field === 3 && wire === 2) pkt.destClient = r.bytes().toString();
    else if (field === 4 && wire === 0) pkt.timeoutTimestamp = r.varint();
    else if (field === 5 && wire === 2) pkt.payloads.push(decodePayload(r.bytes()));
    else r.skip(wire);
  }
  return pkt;
}

const sha256 = (hexOrBytes) => ethers.sha256(hexOrBytes);

// ICS24Host.hashPayload
function hashPayload(p) {
  const packed = ethers.concat([
    sha256(ethers.toUtf8Bytes(p.sourcePort)),
    sha256(ethers.toUtf8Bytes(p.destPort)),
    sha256(ethers.toUtf8Bytes(p.version)),
    sha256(ethers.toUtf8Bytes(p.encoding)),
    sha256(p.value),
  ]);
  return sha256(packed);
}

// ICS24Host.packetCommitmentBytes32
function packetCommitment(pkt) {
  let appBytes = "0x";
  for (const p of pkt.payloads) appBytes = ethers.concat([appBytes, hashPayload(p)]);
  const timeoutBE = ethers.zeroPadValue(ethers.toBeHex(pkt.timeoutTimestamp), 8);
  return sha256(ethers.concat([
    "0x02",
    sha256(ethers.toUtf8Bytes(pkt.destClient)),
    sha256(timeoutBE),
    sha256(appBytes),
  ]));
}

// ICS24Host.packetAcknowledgementCommitmentBytes32 / channeltypesv2.CommitAcknowledgement:
// sha256(0x02 || sha256(ack_1) || ... ). Note the 0x02 prefix (not 0x03) and
// that the concatenated hashes are not themselves re-hashed.
function ackCommitment(acks) {
  let ackBytes = "0x";
  for (const a of acks) ackBytes = ethers.concat([ackBytes, sha256(a)]);
  return sha256(ethers.concat(["0x02", ackBytes]));
}

// hostv2 store keys: <clientId> || 0x01|0x03 || big-endian uint64(sequence)
const packetCommitmentKey = (clientId, seq) =>
  ethers.concat([ethers.toUtf8Bytes(clientId), "0x01", ethers.zeroPadValue(ethers.toBeHex(seq), 8)]);
const packetAckKey = (clientId, seq) =>
  ethers.concat([ethers.toUtf8Bytes(clientId), "0x03", ethers.zeroPadValue(ethers.toBeHex(seq), 8)]);

// Solidity tuple form of the Packet struct.
const toSolidityPacket = (p) => [
  p.sequence,
  p.sourceClient,
  p.destClient,
  p.timeoutTimestamp,
  p.payloads.map((x) => [x.sourcePort, x.destPort, x.version, x.encoding, x.value]),
];

module.exports = {
  decodePacket, hashPayload, packetCommitment, ackCommitment,
  packetCommitmentKey, packetAckKey, toSolidityPacket,
};

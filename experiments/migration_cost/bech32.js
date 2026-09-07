// Minimal bech32 (BIP-173) encode/decode, enough to mint distinct Cosmos
// receiver addresses for the user pool.
//
// Needed because each simulated user should migrate to its OWN Cosmos
// account: crediting a fresh account costs more on the Cosmos side than
// bumping one existing balance N times, so reusing a single receiver would
// understate the per-packet cost the ceiling measurement is trying to find.
//
// Cosmos addresses are plain bech32 (NOT bech32m) over the 5-bit regrouping
// of a 20-byte account address, with the chain's HRP ("cosmos").
const CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l";

const polymod = (values) => {
  const GEN = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3];
  let chk = 1;
  for (const v of values) {
    const top = chk >> 25;
    chk = ((chk & 0x1ffffff) << 5) ^ v;
    for (let i = 0; i < 5; i++) if ((top >> i) & 1) chk ^= GEN[i];
  }
  return chk;
};

const hrpExpand = (hrp) => [
  ...[...hrp].map((c) => c.charCodeAt(0) >> 5),
  0,
  ...[...hrp].map((c) => c.charCodeAt(0) & 31),
];

function convertBits(data, from, to, pad) {
  let acc = 0, bits = 0;
  const ret = [];
  const maxv = (1 << to) - 1;
  for (const value of data) {
    if (value < 0 || value >> from !== 0) throw new Error("convertBits: invalid value");
    acc = (acc << from) | value;
    bits += from;
    while (bits >= to) { bits -= to; ret.push((acc >> bits) & maxv); }
  }
  if (pad) { if (bits > 0) ret.push((acc << (to - bits)) & maxv); }
  else if (bits >= from || ((acc << (to - bits)) & maxv)) throw new Error("convertBits: invalid padding");
  return ret;
}

function encode(hrp, dataBytes) {
  const data = convertBits([...dataBytes], 8, 5, true);
  const chk = polymod([...hrpExpand(hrp), ...data, 0, 0, 0, 0, 0, 0]) ^ 1;
  const checksum = [];
  for (let i = 0; i < 6; i++) checksum.push((chk >> (5 * (5 - i))) & 31);
  return `${hrp}1${[...data, ...checksum].map((d) => CHARSET[d]).join("")}`;
}

function decode(addr) {
  const pos = addr.lastIndexOf("1");
  const hrp = addr.slice(0, pos);
  const data = [...addr.slice(pos + 1)].map((c) => {
    const i = CHARSET.indexOf(c);
    if (i < 0) throw new Error(`bech32: bad char ${c}`);
    return i;
  });
  if (polymod([...hrpExpand(hrp), ...data]) !== 1) throw new Error("bech32: bad checksum");
  return { hrp, bytes: Buffer.from(convertBits(data.slice(0, -6), 5, 8, false)) };
}

module.exports = { encode, decode };

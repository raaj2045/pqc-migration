package security

import (
	"bytes"
	"testing"

	ics23 "github.com/cosmos/ics23/go"
	"github.com/ethereum/go-ethereum/rlp"

	channeltypesv2 "github.com/cosmos/ibc-go/v11/modules/core/04-channel/v2/types"
	commitmenttypes "github.com/cosmos/ibc-go/v11/modules/core/23-commitment/types"
	commitmenttypesv2 "github.com/cosmos/ibc-go/v11/modules/core/23-commitment/types/v2"
)

// ---------------------------------------------------------------------------
// Boundary 4: Merkle proof bytes.
//
// A relayer supplies these; they are attacker-controlled until verification
// succeeds. On this chain the Ethereum proof is opaque to Go (it is forwarded
// to the 08-wasm contract), but the envelope and the ICS23 structure are
// decoded in Go first, so both are fuzzed here.
// ---------------------------------------------------------------------------

// FuzzMerkleProofVerify drives ICS23 merkle proof decoding and then the real
// verification entry point, with a well-formed spec, root and path but
// attacker-controlled proof bytes. Verification must fail with an error for
// any malformed proof, never panic.
func FuzzMerkleProofVerify(f *testing.F) {
	f.Add([]byte{})
	f.Add([]byte{0x0a, 0x00})
	f.Add([]byte{0x0a, 0x02, 0x08, 0x01})
	f.Add(bytes.Repeat([]byte{0xff}, 64))

	root := commitmenttypes.NewMerkleRoot(bytes.Repeat([]byte{0x01}, 32))
	path := commitmenttypesv2.NewMerklePath([]byte("ibc"), []byte("key"))
	specs := commitmenttypes.GetSDKSpecs()

	f.Fuzz(func(t *testing.T, bz []byte) {
		var proof commitmenttypes.MerkleProof
		if err := proof.Unmarshal(bz); err != nil {
			return
		}
		if err := proof.VerifyMembership(specs, root, path, []byte("value")); err == nil {
			t.Fatalf("a fuzzed proof verified against an unrelated root: %x", bz)
		}
		_ = proof.VerifyNonMembership(specs, root, path)
	})
}

// FuzzCommitmentProofUnmarshal drives the raw ICS23 CommitmentProof, the
// structure nested inside a MerkleProof.
func FuzzCommitmentProofUnmarshal(f *testing.F) {
	f.Add([]byte{})
	f.Add([]byte{0x0a, 0x00})
	f.Add(bytes.Repeat([]byte{0x08}, 32))

	f.Fuzz(func(t *testing.T, bz []byte) {
		var cp ics23.CommitmentProof
		if err := cp.Unmarshal(bz); err != nil {
			return
		}
		// Calculate() walks the proof structure; malformed shapes must error,
		// not panic.
		_, _ = cp.Calculate()
	})
}

// FuzzMerklePathUnmarshal drives the v2 merkle path, which carries the
// commitment key the light client checks against.
func FuzzMerklePathUnmarshal(f *testing.F) {
	f.Add([]byte{})
	f.Add([]byte{0x0a, 0x03, 'i', 'b', 'c'})

	f.Fuzz(func(t *testing.T, bz []byte) {
		var mp commitmenttypesv2.MerklePath
		_ = mp.Unmarshal(bz)
	})
}

// ---------------------------------------------------------------------------
// Boundary 5: RLP. The Ethereum light client decodes RLP-encoded account and
// storage proof nodes. go-ethereum's decoder is the same implementation the
// Rust side mirrors, and is reachable from Go in this dependency tree.
// ---------------------------------------------------------------------------

// FuzzRLPDecodeReceipt drives RLP decoding into a receipt-shaped struct:
// wrong length prefixes, truncated lists, oversized declared lengths.
func FuzzRLPDecodeReceipt(f *testing.F) {
	type receipt struct {
		Status            uint64
		CumulativeGasUsed uint64
		Bloom             [256]byte
		Logs              []struct {
			Address [20]byte
			Topics  [][32]byte
			Data    []byte
		}
	}

	f.Add([]byte{})
	f.Add([]byte{0xc0})                   // empty list
	f.Add([]byte{0xc1, 0x80})             // list containing empty string
	f.Add([]byte{0xf8, 0xff})             // declared long length, no payload
	f.Add([]byte{0xb8, 0x40})             // long string prefix, truncated
	f.Add(bytes.Repeat([]byte{0xff}, 16)) // garbage

	f.Fuzz(func(t *testing.T, bz []byte) {
		var r receipt
		_ = rlp.DecodeBytes(bz, &r)
	})
}

// FuzzRLPDecodeProofNodes drives RLP decoding of a Merkle-Patricia trie node
// list, the shape eth_getProof returns and the light client walks.
func FuzzRLPDecodeProofNodes(f *testing.F) {
	f.Add([]byte{})
	f.Add([]byte{0xc0})
	f.Add([]byte{0xc2, 0x80, 0x80})
	f.Add([]byte{0xf7})
	f.Add(bytes.Repeat([]byte{0xb7}, 8))

	f.Fuzz(func(t *testing.T, bz []byte) {
		var nodes [][]byte
		_ = rlp.DecodeBytes(bz, &nodes)
	})
}

// FuzzRLPSplit drives the lowest-level RLP primitive, which every higher
// decoder depends on for length-prefix handling.
func FuzzRLPSplit(f *testing.F) {
	f.Add([]byte{})
	f.Add([]byte{0x80})
	f.Add([]byte{0xb8})
	f.Add([]byte{0xf8, 0x01})
	f.Add([]byte{0xbf, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff})

	f.Fuzz(func(t *testing.T, bz []byte) {
		_, _, _, _ = rlp.Split(bz)
	})
}

// ---------------------------------------------------------------------------
// Boundary 6: the relayer-submitted packet messages themselves. These carry
// the proof bytes and are the outermost attacker-controlled envelope on the
// receive and acknowledge paths.
// ---------------------------------------------------------------------------

// FuzzMsgRecvPacketUnmarshal drives MsgRecvPacket, which carries
// proof_commitment and proof_height from an untrusted relayer.
func FuzzMsgRecvPacketUnmarshal(f *testing.F) {
	f.Add([]byte{})
	f.Add([]byte{0x0a, 0x00})
	f.Add(bytes.Repeat([]byte{0x12}, 24))

	f.Fuzz(func(t *testing.T, bz []byte) {
		var msg channeltypesv2.MsgRecvPacket
		if err := msg.Unmarshal(bz); err != nil {
			return
		}
		_ = msg.ValidateBasic()
	})
}

// FuzzMsgAcknowledgementUnmarshal drives the acknowledgement message on the
// return leg.
func FuzzMsgAcknowledgementUnmarshal(f *testing.F) {
	f.Add([]byte{})
	f.Add([]byte{0x0a, 0x00})
	f.Add(bytes.Repeat([]byte{0x1a}, 24))

	f.Fuzz(func(t *testing.T, bz []byte) {
		var msg channeltypesv2.MsgAcknowledgement
		if err := msg.Unmarshal(bz); err != nil {
			return
		}
		_ = msg.ValidateBasic()
	})
}

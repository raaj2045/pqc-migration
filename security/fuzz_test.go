// Package security holds fuzz targets for every deserialization boundary on
// the live ICS-20 / light-client path that accepts input from an untrusted
// source before that input has been verified.
//
// The contract each target asserts is the same: a malformed input must be
// rejected with an error, never with a panic. A panic on attacker-supplied
// bytes is a denial-of-service surface regardless of whether the caller
// happens to recover, because it converts "reject this message" into
// "abort this execution path".
//
// Run a single target:
//
//	go test ./security/ -run=Fuzz<Name> -fuzz=Fuzz<Name> -fuzztime=5m
//
// Run the seed corpus only (what `go test ./...` does):
//
//	go test ./security/
package security

import (
	"testing"

	"github.com/cosmos/cosmos-sdk/crypto/keys/mldsa65"
	channeltypesv2 "github.com/cosmos/ibc-go/v11/modules/core/04-channel/v2/types"

	"github.com/raaj2045/pqchain-v2/app"

	transfertypes "github.com/cosmos/ibc-go/v11/modules/apps/transfer/types"
)

// ---------------------------------------------------------------------------
// Boundary 1: ML-DSA-65 public keys carried in a transaction's SignerInfo.
//
// A PubKey's Key field carries whatever length the wire supplied, so every
// method that consumes one must tolerate an arbitrary length — or be preceded
// by something that constrains it.
// ---------------------------------------------------------------------------

// FuzzMLDSA65PubKeyGuarded drives the chain's own defence: the ante-level
// length guard in app.RejectMalformedPubKeys.
//
// It asserts the property the chain actually relies on — any key the guard
// admits is safe to hand to Address(). Targeting the guard rather than the
// unguarded upstream function is what keeps this target meaningful, and means
// it keeps passing if upstream changes.
func FuzzMLDSA65PubKeyGuarded(f *testing.F) {
	f.Add([]byte{})           // empty
	f.Add(make([]byte, 1))    // far too short
	f.Add(make([]byte, 1951)) // one byte short of PubKeySize
	f.Add(make([]byte, 1952)) // exactly PubKeySize
	f.Add(make([]byte, 1953)) // one byte long
	f.Add(make([]byte, 4096)) // far too long

	f.Fuzz(func(t *testing.T, key []byte) {
		pk := &mldsa65.PubKey{Key: key}

		if err := app.RejectMalformedPubKeys(txWithPubKey(pk)); err != nil {
			return // rejected at the boundary: Address() is never reached
		}

		// Admitted by the guard, so Address() must be safe.
		if got := len(pk.Address()); got != 20 {
			t.Fatalf("guard admitted a key yielding a %d-byte address", got)
		}
	})
}

// FuzzMLDSA65VerifySignature drives signature verification with arbitrary key
// and signature bytes. Must return false, never panic.
func FuzzMLDSA65VerifySignature(f *testing.F) {
	f.Add([]byte{}, []byte{}, []byte("msg"))
	f.Add(make([]byte, 1952), make([]byte, 3309), []byte("msg")) // correct sizes, zero bytes
	f.Add(make([]byte, 1952), make([]byte, 3308), []byte("msg")) // sig one byte short
	f.Add(make([]byte, 1952), make([]byte, 3310), []byte("msg")) // sig one byte long
	f.Add(make([]byte, 100), make([]byte, 3309), []byte("msg"))  // key too short

	f.Fuzz(func(t *testing.T, key, sig, msg []byte) {
		pk := mldsa65.PubKey{Key: key}
		if pk.VerifySignature(msg, sig) {
			// A zero/garbage signature must never verify.
			t.Fatalf("garbage signature verified: len(key)=%d len(sig)=%d", len(key), len(sig))
		}
	})
}

// FuzzMLDSA65PubKeyUnmarshalAmino drives the amino path, which does check
// length. Included as a control: it should reject cleanly.
func FuzzMLDSA65PubKeyUnmarshalAmino(f *testing.F) {
	f.Add([]byte{})
	f.Add(make([]byte, 1952))
	f.Add(make([]byte, 10))

	f.Fuzz(func(t *testing.T, bz []byte) {
		var pk mldsa65.PubKey
		_ = pk.UnmarshalAmino(bz)
	})
}

// ---------------------------------------------------------------------------
// Boundary 2: ICS-20 packet data. Arrives inside a relayed packet from the
// counterparty chain and is decoded before any application-level validation.
// ---------------------------------------------------------------------------

// FuzzTransferPacketDataJSON drives the JSON decoding path.
func FuzzTransferPacketDataJSON(f *testing.F) {
	f.Add([]byte(`{"denom":"stake","amount":"100","sender":"a","receiver":"b","memo":""}`))
	f.Add([]byte(`{}`))
	f.Add([]byte(`{"amount":"-1"}`))
	f.Add([]byte(``))
	f.Add([]byte(`null`))
	f.Add([]byte(`{"amount":"99999999999999999999999999999999999999"}`))

	f.Fuzz(func(t *testing.T, bz []byte) {
		_, _ = transfertypes.UnmarshalPacketData(bz, transfertypes.V1, transfertypes.EncodingJSON)
	})
}

// FuzzTransferPacketDataABI drives the solidity-ABI decoding path, which is
// the encoding the EVM counterparty actually sends.
func FuzzTransferPacketDataABI(f *testing.F) {
	f.Add([]byte{})
	f.Add(make([]byte, 32))
	f.Add(make([]byte, 64))
	f.Add(make([]byte, 31))  // sub-word length
	f.Add(make([]byte, 320)) // plausible head, no tail

	f.Fuzz(func(t *testing.T, bz []byte) {
		_, _ = transfertypes.DecodeABIFungibleTokenPacketData(bz)
	})
}

// FuzzTransferPacketDataProto drives the protobuf decoding path.
func FuzzTransferPacketDataProto(f *testing.F) {
	f.Add([]byte{})
	f.Add([]byte{0x0a, 0x05, 's', 't', 'a', 'k', 'e'})
	f.Add([]byte{0xff, 0xff, 0xff, 0xff})

	f.Fuzz(func(t *testing.T, bz []byte) {
		_, _ = transfertypes.UnmarshalPacketData(bz, transfertypes.V1, transfertypes.EncodingProtobuf)
	})
}

// ---------------------------------------------------------------------------
// Boundary 3: the IBC v2 packet envelope itself, relayed from the counterparty.
// ---------------------------------------------------------------------------

// FuzzIBCV2PacketUnmarshal drives proto decoding of a channel/v2 Packet, the
// outermost attacker-controlled structure on the relay path.
func FuzzIBCV2PacketUnmarshal(f *testing.F) {
	f.Add([]byte{})
	f.Add([]byte{0x08, 0x01})
	f.Add([]byte{0xff, 0xff, 0xff, 0xff, 0xff})

	f.Fuzz(func(t *testing.T, bz []byte) {
		var p channeltypesv2.Packet
		if err := p.Unmarshal(bz); err != nil {
			return
		}
		// A packet that decodes must also survive stateless validation without
		// panicking; ValidateBasic runs before any state is touched.
		_ = p.ValidateBasic()
	})
}

// FuzzIBCV2AcknowledgementUnmarshal drives the acknowledgement envelope, which
// the counterparty controls on the return leg.
func FuzzIBCV2AcknowledgementUnmarshal(f *testing.F) {
	f.Add([]byte{})
	f.Add([]byte{0x0a, 0x02, 0x01, 0x02})

	f.Fuzz(func(t *testing.T, bz []byte) {
		var a channeltypesv2.Acknowledgement
		if err := a.Unmarshal(bz); err != nil {
			return
		}
		_ = channeltypesv2.CommitAcknowledgement(a)
	})
}

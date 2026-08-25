package app_test

import (
	"testing"

	cmtmldsa65 "github.com/cometbft/cometbft/crypto/mldsa65"

	"github.com/cosmos/cosmos-sdk/codec"
	codectypes "github.com/cosmos/cosmos-sdk/codec/types"
	cryptocodec "github.com/cosmos/cosmos-sdk/crypto/codec"
	"github.com/cosmos/cosmos-sdk/crypto/keys/mldsa65"
	cryptotypes "github.com/cosmos/cosmos-sdk/crypto/types"
	sdk "github.com/cosmos/cosmos-sdk/types"
	"github.com/cosmos/cosmos-sdk/types/tx/signing"
	authtx "github.com/cosmos/cosmos-sdk/x/auth/tx"

	"github.com/raaj2045/pqchain-v2/app"
)

// buildTxWithPubKey assembles a signature-carrying transaction whose single
// SignerInfo advertises pk, exactly as a crafted transaction from the wire
// would. The signature bytes are irrelevant: the guard runs before any
// signature is verified.
func buildTxWithPubKey(t *testing.T, pk cryptotypes.PubKey) sdk.Tx {
	t.Helper()

	registry := codectypes.NewInterfaceRegistry()
	cryptocodec.RegisterInterfaces(registry)
	cdc := codec.NewProtoCodec(registry)
	cfg := authtx.NewTxConfig(cdc, authtx.DefaultSignModes)

	builder := cfg.NewTxBuilder()
	if err := builder.SetSignatures(signing.SignatureV2{
		PubKey:   pk,
		Data:     &signing.SingleSignatureData{SignMode: signing.SignMode_SIGN_MODE_DIRECT},
		Sequence: 0,
	}); err != nil {
		t.Fatalf("SetSignatures: %v", err)
	}
	return builder.GetTx()
}

// TestRejectMalformedPubKeys_WrongLength is the regression test for the panic
// found by security/fuzz_test.go:FuzzMLDSA65PubKeyAddress. Each size below is
// one the fuzzer produced or that bounds the valid size; every one must be
// rejected with an error, and none may panic.
func TestRejectMalformedPubKeys_WrongLength(t *testing.T) {
	for _, tc := range []struct {
		name string
		size int
	}{
		{"empty (minimal fuzzer counterexample)", 0},
		{"one byte", 1},
		{"one byte short", cmtmldsa65.PubKeySize - 1},
		{"one byte long", cmtmldsa65.PubKeySize + 1},
		{"double length", cmtmldsa65.PubKeySize * 2},
	} {
		t.Run(tc.name, func(t *testing.T) {
			pk := &mldsa65.PubKey{Key: make([]byte, tc.size)}
			sdkTx := buildTxWithPubKey(t, pk)

			// Must return an error, and must not panic doing so.
			err := app.RejectMalformedPubKeys(sdkTx)
			if err == nil {
				t.Fatalf("a %d-byte ML-DSA-65 key was accepted; expected rejection", tc.size)
			}
		})
	}
}

// TestRejectMalformedPubKeys_ValidKeyUnaffected confirms the guard is inert for
// well-formed keys: a correctly sized key passes, and its Address is unchanged
// by the presence of the check.
func TestRejectMalformedPubKeys_ValidKeyUnaffected(t *testing.T) {
	pk := &mldsa65.PubKey{Key: make([]byte, cmtmldsa65.PubKeySize)}

	before := pk.Address()

	sdkTx := buildTxWithPubKey(t, pk)
	if err := app.RejectMalformedPubKeys(sdkTx); err != nil {
		t.Fatalf("a well-formed %d-byte key was rejected: %v", cmtmldsa65.PubKeySize, err)
	}

	if after := pk.Address(); string(after) != string(before) {
		t.Fatal("Address() changed for a valid key")
	}
	if len(before) != 20 {
		t.Fatalf("address length = %d, want 20", len(before))
	}
}

// TestRejectMalformedPubKeys_RealKeypair exercises a genuine generated keypair,
// end to end, to confirm the guard does not disturb ordinary signing material.
func TestRejectMalformedPubKeys_RealKeypair(t *testing.T) {
	priv, err := mldsa65.GenPrivKey()
	if err != nil {
		t.Fatalf("GenPrivKey: %v", err)
	}
	pub := priv.PubKey()

	if got := len(pub.Bytes()); got != cmtmldsa65.PubKeySize {
		t.Fatalf("generated pubkey is %d bytes, want %d", got, cmtmldsa65.PubKeySize)
	}

	sdkTx := buildTxWithPubKey(t, pub)
	if err := app.RejectMalformedPubKeys(sdkTx); err != nil {
		t.Fatalf("a generated keypair was rejected: %v", err)
	}

	// A real signature over a real key must still verify: the guard is not in
	// the verification path at all.
	msg := []byte("phase A regression")
	sig, err2 := priv.Sign(msg)
	if err2 != nil {
		t.Fatalf("Sign: %v", err2)
	}
	if !pub.VerifySignature(msg, sig) {
		t.Fatal("a valid signature failed to verify")
	}
}

// TestRejectMalformedPubKeys_NonSigTx confirms a transaction that carries no
// signatures is passed through rather than rejected.
func TestRejectMalformedPubKeys_NonSigTx(t *testing.T) {
	registry := codectypes.NewInterfaceRegistry()
	cryptocodec.RegisterInterfaces(registry)
	cdc := codec.NewProtoCodec(registry)
	cfg := authtx.NewTxConfig(cdc, authtx.DefaultSignModes)

	if err := app.RejectMalformedPubKeys(cfg.NewTxBuilder().GetTx()); err != nil {
		t.Fatalf("a transaction with no signatures was rejected: %v", err)
	}
}

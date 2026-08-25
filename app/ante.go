package app

import (
	cmtmldsa65 "github.com/cometbft/cometbft/crypto/mldsa65"

	errorsmod "cosmossdk.io/errors"

	"github.com/cosmos/cosmos-sdk/crypto/keys/mldsa65"
	sdk "github.com/cosmos/cosmos-sdk/types"
	sdkerrors "github.com/cosmos/cosmos-sdk/types/errors"
	authsigning "github.com/cosmos/cosmos-sdk/x/auth/signing"
)

// RejectMalformedPubKeys rejects a transaction carrying an ML-DSA-65 public key
// whose length is not exactly mldsa65.PubKeySize.
//
// # Why this exists
//
// mldsa65.PubKey.Address() requires a key of exactly PubKeySize bytes, and the
// SDK's ante chain calls Address() on the transaction-supplied key in order to
// compare it against the declared signer. Nothing between the wire and that
// call constrains the key's length. This decorator supplies the missing bound,
// so a wrong-length key is rejected with an error rather than reaching code
// that assumes a well-formed key.
//
// The underlying defect is in stock Cosmos SDK v0.55, which this chain
// deliberately does not fork — running unmodified upstream is the point of the
// rebuild — so the fix cannot live where the bug is. Details of the upstream
// defect are withheld pending coordinated disclosure with its maintainers, and
// are not required to understand or maintain this decorator.
//
// # Maintenance
//
// This must run ahead of the entire SDK ante chain: it is a precondition for
// the decorators that follow, not a check that can be reordered among them.
// app/ante_guard_test.go pins the property the guard depends on — that it
// rejects exactly the keys the downstream call cannot accept — so weakening
// the length comparison here fails that test.
//
// Found by the fuzz harness in security/.
func RejectMalformedPubKeys(tx sdk.Tx) error {
	sigTx, ok := tx.(authsigning.SigVerifiableTx)
	if !ok {
		// Not a signature-carrying transaction; the SDK will reject it later.
		return nil
	}

	pubKeys, err := sigTx.GetPubKeys()
	if err != nil {
		return err
	}

	for i, pk := range pubKeys {
		// A nil entry is legitimate: the signer's key may already be on-chain.
		if pk == nil {
			continue
		}
		key, ok := pk.(*mldsa65.PubKey)
		if !ok {
			continue
		}
		if len(key.Key) != cmtmldsa65.PubKeySize {
			return errorsmod.Wrapf(
				sdkerrors.ErrInvalidPubKey,
				"signer %d: ml-dsa-65 public key must be %d bytes, got %d",
				i, cmtmldsa65.PubKeySize, len(key.Key),
			)
		}
	}

	return nil
}

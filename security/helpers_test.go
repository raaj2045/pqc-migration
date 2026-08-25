package security

import (
	"github.com/cosmos/cosmos-sdk/client"
	"github.com/cosmos/cosmos-sdk/codec"
	codectypes "github.com/cosmos/cosmos-sdk/codec/types"
	cryptocodec "github.com/cosmos/cosmos-sdk/crypto/codec"
	cryptotypes "github.com/cosmos/cosmos-sdk/crypto/types"
	sdk "github.com/cosmos/cosmos-sdk/types"
	"github.com/cosmos/cosmos-sdk/types/tx/signing"
	authtx "github.com/cosmos/cosmos-sdk/x/auth/tx"
)

// txConfig is built once: constructing an interface registry per fuzz iteration
// would dominate the runtime and starve the fuzzer of executions.
var txConfig = func() client.TxConfig {
	registry := codectypes.NewInterfaceRegistry()
	cryptocodec.RegisterInterfaces(registry)
	return authtx.NewTxConfig(codec.NewProtoCodec(registry), authtx.DefaultSignModes)
}()

// txWithPubKey wraps pk in a signature-carrying transaction, the shape a
// crafted transaction from the wire would take.
func txWithPubKey(pk cryptotypes.PubKey) sdk.Tx {
	builder := txConfig.NewTxBuilder()
	if err := builder.SetSignatures(signing.SignatureV2{
		PubKey:   pk,
		Data:     &signing.SingleSignatureData{SignMode: signing.SignMode_SIGN_MODE_DIRECT},
		Sequence: 0,
	}); err != nil {
		// Only reachable on a programming error in the harness itself.
		panic(err)
	}
	return builder.GetTx()
}

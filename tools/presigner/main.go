// presigner generates pre-signed Cosmos MsgSend transactions for the
// validator_scaling_v2 experiment.
//
// Two-mode flow used by the experiment harness:
//
//  1. emit-addresses
//     Deterministically derives M private keys for the chosen scheme
//     (secp256k1 or mldsa44) from a fixed prefix + sender index, prints
//     one JSON record per sender (name, address) so init_testnet.sh can
//     add-genesis-account them.
//
//  2. sign
//     Re-derives the same M keys, then for each sender produces N
//     MsgSend transactions with sequence 0..N-1 and account number
//     base + sender_idx. Output is JSONL: one record per tx with
//     {sender_idx, sequence, tx_b64}.
//
// Determinism: identical (--scheme, --senders, --seed-prefix) input
// yields identical addresses. init_testnet.sh's add-genesis-account
// ordering must match the sender index so account_num = base + idx
// holds at chain init.
package main

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"sync"

	"github.com/cosmos/cosmos-sdk/client"
	"github.com/cosmos/cosmos-sdk/codec"
	codectypes "github.com/cosmos/cosmos-sdk/codec/types"
	cryptocodec "github.com/cosmos/cosmos-sdk/crypto/codec"
	"github.com/cosmos/cosmos-sdk/crypto/keys/mldsa"
	"github.com/cosmos/cosmos-sdk/crypto/keys/secp256k1"
	cryptotypes "github.com/cosmos/cosmos-sdk/crypto/types"
	sdk "github.com/cosmos/cosmos-sdk/types"
	signingtypes "github.com/cosmos/cosmos-sdk/types/tx/signing"
	authsigning "github.com/cosmos/cosmos-sdk/x/auth/signing"
	authtx "github.com/cosmos/cosmos-sdk/x/auth/tx"
	banktypes "github.com/cosmos/cosmos-sdk/x/bank/types"
)

const (
	defaultGasLimit    = 100_000
	defaultSeedPrefix  = "cosmos_validator_scaling_v2"
	defaultSendAmount  = 1 // 1 stake per tx, easily covered by genesis funding
	defaultSendDenom   = "stake"
	bech32AccountPfx   = "cosmos"
	bech32ValidatorPfx = "cosmosvaloper"
)

type addressRecord struct {
	Name    string `json:"name"`
	Index   int    `json:"index"`
	Address string `json:"address"`
}

type signedTxRecord struct {
	SenderIdx int    `json:"sender_idx"`
	Sequence  uint64 `json:"sequence"`
	TxB64     string `json:"tx_b64"`
}

type sender struct {
	idx     int
	name    string
	priv    cryptotypes.PrivKey
	address string
	acctNum uint64
}

func main() {
	if len(os.Args) < 2 {
		usage()
	}
	configureSDK()

	mode := os.Args[1]
	args := os.Args[2:]
	switch mode {
	case "emit-addresses":
		runEmitAddresses(args)
	case "sign":
		runSign(args)
	default:
		usage()
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, `presigner: validator_scaling_v2 pre-signed tx generator

Usage:
  presigner emit-addresses --scheme {secp256k1|mldsa44} --senders N
                           [--seed-prefix STR] [--out FILE]

  presigner sign --scheme {secp256k1|mldsa44} --senders N --txs-per-sender K
                 --chain-id ID --account-num-base BASE [--seed-prefix STR]
                 [--gas-limit G] [--out FILE]`)
	os.Exit(2)
}

// configureSDK pins the bech32 account/validator prefixes for AccAddress.String()
// before any address bytes are formatted. The default in cosmos-sdk is
// "cosmos" already; we set it explicitly so the binary is self-contained.
func configureSDK() {
	cfg := sdk.GetConfig()
	cfg.SetBech32PrefixForAccount(bech32AccountPfx, bech32AccountPfx+"pub")
	cfg.SetBech32PrefixForValidator(bech32ValidatorPfx, bech32ValidatorPfx+"pub")
	// Do not Seal — keeps the binary callable in tests / repeated process runs.
}

func runEmitAddresses(args []string) {
	fs := flag.NewFlagSet("emit-addresses", flag.ExitOnError)
	scheme := fs.String("scheme", "", "secp256k1 or mldsa44")
	senders := fs.Int("senders", 0, "number of sender keys to derive")
	seedPrefix := fs.String("seed-prefix", defaultSeedPrefix, "deterministic seed prefix")
	outPath := fs.String("out", "-", "output JSONL path or '-' for stdout")
	_ = fs.Parse(args)

	if *scheme == "" || *senders <= 0 {
		usage()
	}

	out, closer := openOut(*outPath)
	defer closer()

	enc := json.NewEncoder(out)
	for i := 0; i < *senders; i++ {
		priv := deriveKey(*scheme, *seedPrefix, i)
		rec := addressRecord{
			Name:    fmt.Sprintf("loadgen_%s_%d", *scheme, i),
			Index:   i,
			Address: bech32AccountAddress(priv.PubKey()),
		}
		if err := enc.Encode(rec); err != nil {
			die("encode: %v", err)
		}
	}
	fmt.Fprintf(os.Stderr, "presigner: wrote %d %s addresses\n", *senders, *scheme)
}

func runSign(args []string) {
	fs := flag.NewFlagSet("sign", flag.ExitOnError)
	scheme := fs.String("scheme", "", "secp256k1 or mldsa44")
	senders := fs.Int("senders", 0, "number of sender keys to derive")
	txsPer := fs.Int("txs-per-sender", 0, "transactions per sender")
	chainID := fs.String("chain-id", "", "chain id")
	acctBase := fs.Uint64("account-num-base", 0, "account number for sender 0; sender i uses base+i")
	seedPrefix := fs.String("seed-prefix", defaultSeedPrefix, "deterministic seed prefix")
	gasLimit := fs.Uint64("gas-limit", defaultGasLimit, "gas limit per tx")
	outPath := fs.String("out", "-", "output JSONL path or '-' for stdout")
	_ = fs.Parse(args)

	if *scheme == "" || *senders <= 0 || *txsPer <= 0 || *chainID == "" {
		usage()
	}

	cdc := newCodec()
	txConfig := authtx.NewTxConfig(cdc, authtx.DefaultSignModes)

	senderList := make([]*sender, *senders)
	for i := 0; i < *senders; i++ {
		priv := deriveKey(*scheme, *seedPrefix, i)
		senderList[i] = &sender{
			idx:     i,
			name:    fmt.Sprintf("loadgen_%s_%d", *scheme, i),
			priv:    priv,
			address: bech32AccountAddress(priv.PubKey()),
			acctNum: *acctBase + uint64(i),
		}
	}
	// Recipient: send to sender 0, keeps every tx self-contained inside the arm.
	recipient := senderList[0].address

	out, closer := openOut(*outPath)
	defer closer()

	var writeMu sync.Mutex
	enc := json.NewEncoder(out)

	var wg sync.WaitGroup
	errCh := make(chan error, *senders)
	ctx := context.Background()
	for _, s := range senderList {
		wg.Add(1)
		go func(s *sender) {
			defer wg.Done()
			for seq := uint64(0); seq < uint64(*txsPer); seq++ {
				txBytes, err := buildAndSignMsgSend(
					ctx, txConfig, s, recipient,
					*chainID, *gasLimit, seq,
				)
				if err != nil {
					errCh <- fmt.Errorf("sender %d seq %d: %w", s.idx, seq, err)
					return
				}
				rec := signedTxRecord{
					SenderIdx: s.idx,
					Sequence:  seq,
					TxB64:     base64.StdEncoding.EncodeToString(txBytes),
				}
				writeMu.Lock()
				err = enc.Encode(rec)
				writeMu.Unlock()
				if err != nil {
					errCh <- fmt.Errorf("write sender %d seq %d: %w", s.idx, seq, err)
					return
				}
			}
		}(s)
	}
	wg.Wait()
	close(errCh)
	for err := range errCh {
		die("%v", err)
	}

	fmt.Fprintf(os.Stderr,
		"presigner: signed %d txs (%d senders × %d) scheme=%s chain=%s acct_base=%d\n",
		(*senders)*(*txsPer), *senders, *txsPer, *scheme, *chainID, *acctBase)
}

// buildAndSignMsgSend builds a single MsgSend tx and signs it with the
// sender's private key using SIGN_MODE_DIRECT. Returns the encoded tx bytes
// suitable for raw broadcast via Tendermint /broadcast_tx_sync.
func buildAndSignMsgSend(
	ctx context.Context,
	txConfig client.TxConfig,
	s *sender, recipient, chainID string,
	gasLimit, sequence uint64,
) ([]byte, error) {
	fromAddr, err := sdk.AccAddressFromBech32(s.address)
	if err != nil {
		return nil, fmt.Errorf("parse from: %w", err)
	}
	toAddr, err := sdk.AccAddressFromBech32(recipient)
	if err != nil {
		return nil, fmt.Errorf("parse to: %w", err)
	}
	msg := banktypes.NewMsgSend(
		fromAddr,
		toAddr,
		sdk.NewCoins(sdk.NewInt64Coin(defaultSendDenom, defaultSendAmount)),
	)

	txBuilder := txConfig.NewTxBuilder()
	if err := txBuilder.SetMsgs(msg); err != nil {
		return nil, fmt.Errorf("set msgs: %w", err)
	}
	txBuilder.SetGasLimit(gasLimit)
	txBuilder.SetFeeAmount(sdk.NewCoins())
	txBuilder.SetMemo("")

	// Round 1: set signature with empty signature bytes so the signer info
	// (pub key + sign mode) is encoded into the tx body before sign-bytes
	// computation. This is the standard two-pass dance for SIGN_MODE_DIRECT.
	emptySig := signingtypes.SignatureV2{
		PubKey: s.priv.PubKey(),
		Data: &signingtypes.SingleSignatureData{
			SignMode:  signingtypes.SignMode_SIGN_MODE_DIRECT,
			Signature: nil,
		},
		Sequence: sequence,
	}
	if err := txBuilder.SetSignatures(emptySig); err != nil {
		return nil, fmt.Errorf("set empty sig: %w", err)
	}

	signerData := authsigning.SignerData{
		Address:       s.address,
		ChainID:       chainID,
		AccountNumber: s.acctNum,
		Sequence:      sequence,
		PubKey:        s.priv.PubKey(),
	}
	signBytes, err := authsigning.GetSignBytesAdapter(
		ctx, txConfig.SignModeHandler(),
		signingtypes.SignMode_SIGN_MODE_DIRECT,
		signerData, txBuilder.GetTx(),
	)
	if err != nil {
		return nil, fmt.Errorf("get sign bytes: %w", err)
	}

	signature, err := s.priv.Sign(signBytes)
	if err != nil {
		return nil, fmt.Errorf("priv.Sign: %w", err)
	}

	// Round 2: replace empty signature with the real one.
	finalSig := signingtypes.SignatureV2{
		PubKey: s.priv.PubKey(),
		Data: &signingtypes.SingleSignatureData{
			SignMode:  signingtypes.SignMode_SIGN_MODE_DIRECT,
			Signature: signature,
		},
		Sequence: sequence,
	}
	if err := txBuilder.SetSignatures(finalSig); err != nil {
		return nil, fmt.Errorf("set final sig: %w", err)
	}

	txBytes, err := txConfig.TxEncoder()(txBuilder.GetTx())
	if err != nil {
		return nil, fmt.Errorf("encode tx: %w", err)
	}
	return txBytes, nil
}

// newCodec builds the minimum InterfaceRegistry the presigner needs:
// crypto pubkeys (so SignerInfo can pack the pubkey into Any) and bank
// MsgSend (so TxBody can pack the message). banktypes.RegisterInterfaces
// implicitly registers the sdk.Msg interface via RegisterImplementations.
func newCodec() codec.Codec {
	registry := codectypes.NewInterfaceRegistry()
	cryptocodec.RegisterInterfaces(registry)
	banktypes.RegisterInterfaces(registry)
	return codec.NewProtoCodec(registry)
}

// deriveKey returns a deterministic private key for (scheme, prefix, index).
// SHA-256 of the formatted seed string gives 32 bytes, which both
// secp256k1.GenPrivKeyFromSecret and mldsa.NewPrivKeyFromSecret accept.
func deriveKey(scheme, prefix string, index int) cryptotypes.PrivKey {
	seed := sha256.Sum256([]byte(fmt.Sprintf("%s|%s|%d", prefix, scheme, index)))
	switch scheme {
	case "secp256k1":
		return secp256k1.GenPrivKeyFromSecret(seed[:])
	case "mldsa44":
		k, err := mldsa.NewPrivKeyFromSecret(seed[:])
		if err != nil {
			die("mldsa key derive: %v", err)
		}
		return k
	default:
		die("unsupported scheme: %s", scheme)
		return nil
	}
}

func bech32AccountAddress(pub cryptotypes.PubKey) string {
	return sdk.AccAddress(pub.Address().Bytes()).String()
}

func openOut(path string) (io.Writer, func()) {
	if path == "-" {
		return os.Stdout, func() {}
	}
	f, err := os.Create(path)
	if err != nil {
		die("open output: %v", err)
	}
	return f, func() { _ = f.Close() }
}

func die(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "presigner: "+format+"\n", args...)
	os.Exit(1)
}

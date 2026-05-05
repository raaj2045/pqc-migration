package mldsa

import (
	"bytes"
	"errors"
	"fmt"

	"github.com/cloudflare/circl/tree/main/sign/slhdsa"
	cmtcrypto "github.com/cometbft/cometbft/crypto"
	cryptotypes "github.com/cosmos/cosmos-sdk/crypto/types"
)

var (
	_ cryptotypes.PrivKey = (*PrivKey)(nil)
	_ cryptotypes.PubKey  = (*PubKey)(nil)
)

// GenPrivKey generates a new SLH DSA private key
func GenPrivKey() (*PrivKey, error) {
	_, privKey, err := slhdsa.GenerateKey(nil)
	if err != nil {
		return nil, err
	}

	keyBytes, err := privKey.MarshalBinary()
	if err != nil {
		return nil, fmt.Errorf("failed to marshal private key: %w", err)
	}

	return &PrivKey{Key: keyBytes}, nil
}

// NewPrivKeyFromSecret creates a slhdsa private key from a 32-byte seed
func NewPrivKeyFromSecret(secret []byte) (*PrivKey, error) {
	if len(secret) != slhdsa.SeedSize {
		return nil, fmt.Errorf("secret must be %d bytes for slhdsa, got %d", slhdsa.SeedSize, len(secret))
	}

	var seed [slhdsa.SeedSize]byte
	copy(seed[:], secret)
	_, privKey := slhdsa.NewKeyFromSeed(&seed)

	keyBytes, err := privKey.MarshalBinary()
	if err != nil {
		return nil, fmt.Errorf("failed to marshal private key: %w", err)
	}

	return &PrivKey{Key: keyBytes}, nil
}

// PrivKey methods
func (pk *PrivKey) PubKey() cryptotypes.PubKey {
	if pk == nil || pk.Key == nil {
		return nil
	}

	var privKey slhdsa.PrivateKey
	if err := privKey.UnmarshalBinary(pk.Key); err != nil {
		return nil
	}

	pub := privKey.Public()
	pubKey, ok := pub.(*slhdsa.PublicKey)
	if !ok {
		return nil
	}

	pubKeyBytes, err := pubKey.MarshalBinary()
	if err != nil {
		return nil
	}

	return &PubKey{Key: pubKeyBytes}
}

func (pk *PrivKey) Bytes() []byte {
	if pk == nil {
		return nil
	}
	return pk.Key
}

func (pk *PrivKey) Sign(msg []byte) ([]byte, error) {
	if pk == nil || pk.Key == nil {
		return nil, errors.New("private key is nil")
	}

	var privKey slhdsa.PrivateKey
	if err := privKey.UnmarshalBinary(pk.Key); err != nil {
		return nil, fmt.Errorf("failed to unmarshal private key: %w", err)
	}

	// Use the Scheme().Sign() method (simplest and most compatible)
	signature := privKey.Scheme().Sign(&privKey, msg, nil)
	return signature, nil
}

func (pk *PrivKey) Type() string {
	return "tendermint/PrivKeySLHDSA"
}

func (pk *PrivKey) Equals(other cryptotypes.LedgerPrivKey) bool {
	pk2, ok := other.(*PrivKey)
	if !ok {
		return false
	}
	if pk == nil && pk2 == nil {
		return true
	}
	if pk == nil || pk2 == nil {
		return false
	}
	return bytes.Equal(pk.Bytes(), pk2.Bytes())
}

// PubKey methods
func (pk *PubKey) Address() cmtcrypto.Address {
	if pk == nil || pk.Key == nil {
		return nil
	}
	return cmtcrypto.AddressHash(pk.Key)
}

func (pk *PubKey) Bytes() []byte {
	if pk == nil {
		return nil
	}
	return pk.Key
}

func (pk *PubKey) VerifySignature(msg, sig []byte) bool {
	if pk == nil || pk.Key == nil {
		return false
	}

	var pubKey slhdsa.PublicKey
	if err := pubKey.UnmarshalBinary(pk.Key); err != nil {
		return false
	}

	// CORRECT parameter order: Verify(publicKey, message, context, signature)
	return slhdsa.Verify(&pubKey, msg, nil, sig)
}

func (pk *PubKey) Type() string {
	return "tendermint/PubKeySLHDSA"
}

func (pk *PubKey) Equals(other cryptotypes.PubKey) bool {
	pk2, ok := other.(*PubKey)
	if !ok {
		return false
	}
	return bytes.Equal(pk.Key, pk2.Key)
}

func (pk *PubKey) String() string {
	if pk.Key == nil || len(pk.Key) < 20 {
		return "PubKeySLHDSA{<invalid>}"
	}
	return fmt.Sprintf("PubKeySLHDSA{%X...}", pk.Key[:20])
}

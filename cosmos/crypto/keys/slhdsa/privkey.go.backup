package mldsa

import (
	"errors"
	"fmt"

	"github.com/cloudflare/circl/sign/mldsa/mldsa44"
	cryptotypes "github.com/cosmos/cosmos-sdk/crypto/types"
)

// Use the generated PrivKey struct instead of custom PrivKeyMLDSA44
// The PrivKey struct is already defined in your protobuf generated code

// Ensure PrivKey implements cryptotypes.PrivKey
var _ cryptotypes.PrivKey = (*PrivKey)(nil)

// GenPrivKey generates a new mldsa44 private key. It uses operating system randomness.
func GenPrivKey() (*PrivKey, error) {
	_, privKey, err := mldsa44.GenerateKey(nil)
	if err != nil {
		return nil, err
	}

	// Use MarshalBinary() for proper storage format
	keyBytes, err := privKey.MarshalBinary()
	if err != nil {
		return nil, fmt.Errorf("failed to marshal private key: %w", err)
	}

	return &PrivKey{Key: keyBytes}, nil
}

// NewPrivKeyFromSecret creates a mldsa44 private key from a secret seed (must be 32 bytes).
func NewPrivKeyFromSecret(secret []byte) (*PrivKey, error) {
	if len(secret) != mldsa44.SeedSize {
		return nil, fmt.Errorf("secret must be %d bytes for mldsa44, got %d", mldsa44.SeedSize, len(secret))
	}
	var seed [mldsa44.SeedSize]byte
	copy(seed[:], secret)
	_, privKey := mldsa44.NewKeyFromSeed(&seed)

	// Use MarshalBinary() for proper storage format
	keyBytes, err := privKey.MarshalBinary()
	if err != nil {
		return nil, fmt.Errorf("failed to marshal private key: %w", err)
	}

	return &PrivKey{Key: keyBytes}, nil
}

// PubKey implements SDK PrivKey interface.
func (m *PrivKey) PubKey() cryptotypes.PubKey {
	if m == nil || m.Key == nil {
		return nil
	}

	privKey := new(mldsa44.PrivateKey)
	if err := privKey.UnmarshalBinary(m.Key); err != nil {
		return nil
	}

	pub := privKey.Public()
	if pub == nil {
		return nil
	}

	pubKey, ok := pub.(*mldsa44.PublicKey)
	if !ok {
		return nil
	}

	// Use MarshalBinary() for public key as well
	pubKeyBytes, err := pubKey.MarshalBinary()
	if err != nil {
		return nil
	}

	return &PubKey{Key: pubKeyBytes}
}

// Type returns key type name. Implements SDK PrivKey interface.
func (m *PrivKey) Type() string {
	return "tendermint/PrivKeyMLDSA44"
}

// Sign hashes and signs the message using mldsa44. Implements sdk.PrivKey interface.
func (m *PrivKey) Sign(msg []byte) ([]byte, error) {
	if m == nil || m.Key == nil {
		return nil, errors.New("private key is nil")
	}

	privKey := new(mldsa44.PrivateKey)
	if err := privKey.UnmarshalBinary(m.Key); err != nil {
		return nil, fmt.Errorf("failed to unmarshal private key: %w", err)
	}

	signature := privKey.Scheme().Sign(privKey, msg, nil)
	return signature, nil
}

// Bytes serialize the private key.
func (m *PrivKey) Bytes() []byte {
	if m == nil {
		return nil
	}
	return m.Key
}

// Equals implements SDK PrivKey interface.
func (m *PrivKey) Equals(other cryptotypes.LedgerPrivKey) bool {
	sk2, ok := other.(*PrivKey)
	if !ok {
		return false
	}
	if m == nil && sk2 == nil {
		return true
	}
	if m == nil || sk2 == nil {
		return false
	}
	return string(m.Bytes()) == string(sk2.Bytes())
}

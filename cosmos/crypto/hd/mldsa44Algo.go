package hd

import (
	"crypto/sha256"
	"fmt"
	"log"

	"github.com/cosmos/cosmos-sdk/crypto/keys/mldsa"
	types "github.com/cosmos/cosmos-sdk/crypto/types"
	"github.com/cosmos/go-bip39"
)

const mldsa44Type = PubKeyType("mldsa44")

var Mldsa44 = mldsa44Algo{}

type mldsa44Algo struct{}

func (m mldsa44Algo) Name() PubKeyType {
	return mldsa44Type
}

func (m mldsa44Algo) Derive() DeriveFn {
	return func(mnemonic string, bip39Passphrase, hdPath string) ([]byte, error) {
		log.Printf("MLDSA44 Derive called with mnemonic length: %d, hdPath: %s", len(mnemonic), hdPath)

		seed, err := bip39.NewSeedWithErrorChecking(mnemonic, bip39Passphrase)
		if err != nil {
			log.Printf("MLDSA44 Derive error creating seed: %v", err)
			return nil, err
		}
		log.Printf("MLDSA44 Derive seed created, length: %d", len(seed))

		masterPriv, ch := ComputeMastersFromSeed(seed)
		log.Printf("MLDSA44 Derive masterPriv length: %d", len(masterPriv))

		var derivedKey []byte
		if len(hdPath) == 0 {
			derivedKey = masterPriv[:]
		} else {
			derivedKey, err = DerivePrivateKeyForPath(masterPriv, ch, hdPath)
			if err != nil {
				log.Printf("MLDSA44 Derive error deriving key: %v", err)
				return nil, err
			}
		}

		log.Printf("MLDSA44 Derive derivedKey length: %d", len(derivedKey))

		// Ensure we have exactly 32 bytes for MLDSA44
		var seed32 []byte
		if len(derivedKey) == 32 {
			seed32 = derivedKey
		} else {
			// Hash the input to get exactly 32 bytes
			hash := sha256.Sum256(derivedKey)
			seed32 = hash[:]
		}

		log.Printf("MLDSA44 Derive final seed32 length: %d", len(seed32))
		return seed32, nil
	}
}

func (m mldsa44Algo) Generate() GenerateFn {
	return func(bz []byte) types.PrivKey {
		log.Printf("MLDSA44 Generate called with input length: %d", len(bz))

		if bz == nil {
			log.Printf("MLDSA44 Generate ERROR: input is nil")
			panic("MLDSA44 Generate: input bytes are nil")
		}

		// Ensure we have exactly 32 bytes for MLDSA44
		var seed []byte
		if len(bz) == 32 {
			seed = bz
		} else {
			log.Printf("MLDSA44 Generate: input not 32 bytes, hashing to get 32 bytes")
			// Hash the input to get exactly 32 bytes
			hash := sha256.Sum256(bz)
			seed = hash[:]
		}

		log.Printf("MLDSA44 Generate calling NewPrivKeyFromSecret with seed length: %d", len(seed))

		key, err := mldsa.NewPrivKeyFromSecret(seed)
		if err != nil {
			log.Printf("MLDSA44 Generate ERROR creating private key: %v", err)
			panic(fmt.Sprintf("MLDSA44 Generate: failed to create private key: %v", err))
		}

		if key == nil {
			log.Printf("MLDSA44 Generate ERROR: NewPrivKeyFromSecret returned nil")
			panic("MLDSA44 Generate: NewPrivKeyFromSecret returned nil")
		}

		log.Printf("MLDSA44 Generate SUCCESS: created private key")
		return key
	}
}

package app_test

import (
	"testing"

	cmtmldsa65 "github.com/cometbft/cometbft/crypto/mldsa65"

	"github.com/cosmos/cosmos-sdk/crypto/keys/mldsa65"

	"github.com/raaj2045/pqchain-v2/app"
)

// TestGuardPreemptsAddressPanic demonstrates the property the fix actually
// depends on: for every input the guard rejects, calling Address() directly
// would have panicked. That is what makes the guard sufficient — it is not
// merely returning an error alongside a still-reachable panic, it is covering
// exactly the panicking domain.
func TestGuardPreemptsAddressPanic(t *testing.T) {
	sizes := []int{0, 1, 31, cmtmldsa65.PubKeySize - 1, cmtmldsa65.PubKeySize + 1, 4096}

	for _, size := range sizes {
		pk := &mldsa65.PubKey{Key: make([]byte, size)}

		// 1. The guard rejects it.
		if err := app.RejectMalformedPubKeys(buildTxWithPubKey(t, pk)); err == nil {
			t.Fatalf("size %d: guard accepted a malformed key", size)
		}

		// 2. Address() on that same key panics, i.e. the guard is what stands
		//    between the wire and the panic.
		func() {
			defer func() {
				if r := recover(); r == nil {
					t.Errorf("size %d: Address() no longer panics — upstream may have "+
						"been fixed, in which case this guard can be retired", size)
				}
			}()
			_ = pk.Address()
		}()
	}
}

// TestGuardAcceptsExactlyValidLength pins the boundary: only PubKeySize passes,
// and at that size Address() is safe.
func TestGuardAcceptsExactlyValidLength(t *testing.T) {
	pk := &mldsa65.PubKey{Key: make([]byte, cmtmldsa65.PubKeySize)}
	if err := app.RejectMalformedPubKeys(buildTxWithPubKey(t, pk)); err != nil {
		t.Fatalf("valid key rejected: %v", err)
	}
	if got := len(pk.Address()); got != 20 {
		t.Fatalf("address length %d, want 20", got)
	}
}

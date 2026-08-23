package app

import (
	"testing"

	dbm "github.com/cosmos/cosmos-db"
)

// TestEmptyValueDBGet pins the wrapper's contract against the real goleveldb
// backend, which is the backend that mangles empty values (see emptyvaluedb.go).
func TestEmptyValueDBGet(t *testing.T) {
	raw, err := dbm.NewGoLevelDB("emptyvaluedb_test", t.TempDir(), nil)
	if err != nil {
		t.Fatalf("open goleveldb: %v", err)
	}
	defer raw.Close()
	db := wrapEmptyValueDB(raw)

	// A zero-length value written through the wrapper must read back as a
	// non-nil empty slice — this is the exact case iavl's GetRoot depends on
	// for empty-store roots, and the case raw goleveldb gets wrong.
	if err := db.Set([]byte("empty-root"), []byte{}); err != nil {
		t.Fatalf("set: %v", err)
	}
	v, err := db.Get([]byte("empty-root"))
	if err != nil {
		t.Fatalf("get: %v", err)
	}
	if v == nil {
		t.Fatal("Get returned nil for an existing key with an empty value; historical queries on empty stores would fail")
	}
	if len(v) != 0 {
		t.Fatalf("Get returned %d bytes, want 0", len(v))
	}

	// A genuinely missing key must still return nil, since iavl and the SDK
	// use nil to mean "not found".
	v, err = db.Get([]byte("never-written"))
	if err != nil {
		t.Fatalf("get missing: %v", err)
	}
	if v != nil {
		t.Fatalf("Get returned non-nil (%x) for a missing key, want nil", v)
	}

	// Sanity: normal values pass through untouched.
	if err := db.Set([]byte("k"), []byte("v")); err != nil {
		t.Fatalf("set: %v", err)
	}
	v, err = db.Get([]byte("k"))
	if err != nil || string(v) != "v" {
		t.Fatalf("Get(k) = %q, %v; want \"v\", nil", v, err)
	}
}

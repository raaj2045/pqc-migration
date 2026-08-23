package app

import (
	dbm "github.com/cosmos/cosmos-db"
)

// emptyValueDB restores the nil-vs-empty distinction that goleveldb destroys
// for zero-length values. DO NOT REMOVE — removing this wrapper silently
// breaks ALL historical-height queries (every module, every height) on any
// chain that mounts a store whose IAVL tree is empty, e.g. the 08-wasm store
// before any wasm light-client code is stored.
//
// Why it exists:
//   - IAVL persists an empty tree's per-version root as a zero-length value:
//     SaveEmptyRoot does batch.Set(rootKey, []byte{})
//     (github.com/cosmos/iavl@v1.2.8 nodedb.go:1043-1047).
//   - IAVL's GetRoot then relies on Get distinguishing "key missing" (nil)
//     from "key present with empty value" (non-nil, len 0): nil is treated as
//     ErrVersionDoesNotExist, empty as a legitimate empty root
//     (github.com/cosmos/iavl@v1.2.8 nodedb.go:987-1009).
//   - goleveldb (v1.0.1-0.20220721030215-126854af5e6d, pinned via cosmos-db)
//     copies every Get result with append([]byte(nil), value...), which for a
//     zero-length value returns nil, erasing exactly that distinction
//     (github.com/syndtr/goleveldb leveldb/db.go:786 and :798). cosmos-db's
//     GoLevelDB.Get, PrefixDB.Get, and the SDK store/v2 DBWrapper all pass
//     the nil through unchanged.
//   - Result without this wrapper: GetImmutable on the empty store fails with
//     "version does not exist" even though the root key IS on disk, and
//     CacheMultiStoreWithVersion aborts the whole multi-store query, so every
//     query at any height returns "failed to load state at height N".
//   - Upstream iavl (checked through v1.3.6) has the identical fragile code,
//     so bumping iavl does not fix this.
//
// The wrapper only changes the result for keys that exist with a zero-length
// value: a nil Get result is double-checked with an existence probe, and
// rewritten to a non-nil empty slice when the key is present. Genuinely
// missing keys still return nil. The probe must be an iterator, NOT Has:
// cosmos-db's GoLevelDB.Has is implemented as Get(key) != nil
// (cosmos-db@v1.1.3 goleveldb.go:71-77) and inherits the same mangling,
// while iterators surface empty-value keys correctly (which is also why
// iavl's iterator-based VersionExists said true while GetRoot failed).
// On-disk bytes are untouched, so this has no consensus or store-format
// impact. Regression-tested in emptyvaluedb_test.go.
type emptyValueDB struct {
	dbm.DB
}

// wrapEmptyValueDB wraps db so Get never returns nil for an existing key.
func wrapEmptyValueDB(db dbm.DB) dbm.DB {
	return emptyValueDB{DB: db}
}

// Get returns a non-nil empty slice for keys stored with zero-length values.
func (d emptyValueDB) Get(key []byte) ([]byte, error) {
	v, err := d.DB.Get(key)
	if err != nil || v != nil {
		return v, err
	}
	// nil can mean "missing" or "empty value mangled by goleveldb": probe
	// existence with an iterator over [key, key||0x00), which is exactly key.
	end := make([]byte, len(key)+1)
	copy(end, key)
	itr, err := d.DB.Iterator(key, end)
	if err != nil {
		return nil, err
	}
	defer itr.Close()
	if itr.Valid() {
		return []byte{}, nil
	}
	return nil, nil
}

// Has mirrors the fixed Get so empty-value keys report as present.
func (d emptyValueDB) Has(key []byte) (bool, error) {
	v, err := d.Get(key)
	if err != nil {
		return false, err
	}
	return v != nil, nil
}

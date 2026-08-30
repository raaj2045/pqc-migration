# wasmvm data directory

`WasmConfig.DataDir` is `<home>/ibc_08-wasm_client_data` and **must stay stable
across restarts**. Stored wasm code lives there as well as in the KVStore, and
`InitializePinnedCodes` reads the files back on every startup.

## Why it is not randomized

wasmvm takes an exclusive lock on its data directory, and `root.go` builds a
throwaway app just to read the encoding config before the real server app
starts. Pointing both at the same home deadlocks the second instantiation.

An earlier version dodged this by appending a random suffix to the directory
name. That worked only while no code had been stored — once `MsgStoreCode` had
run, the next restart looked in a fresh empty directory and panicked:

```
panic: failed to initialize pinned codes: Error calling the VM:
Cache error: Error opening Wasm file for reading
```

The fix is the other way round: the directory is deterministic, and the
throwaway app in `root.go` gets its own temporary home (`os.MkdirTemp`), so it
can never contend for the node's lock.

## Recovering a chain that hit the old bug

If a node has stored code under a randomized directory, the wasm blob is still
on disk. Move it into the deterministic location before restarting:

```bash
cd <home>/ibc_08-wasm_client_data
# find the subdirectory that actually holds a .wasm file
find . -name '*.wasm'
cp -r <that-dir>/state/* state/ && cp -r <that-dir>/cache/* cache/
rm -rf [0-9]*/
```

---

[Project README](../README.md) · [Getting started](getting-started.md) · [Architecture](architecture.md)

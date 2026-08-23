# Building

```bash
go build ./...
go test ./...
```

`devnet/` holds JavaScript and Python tooling only. It is deliberately not a Go
package and is invisible to `go build ./...`.

## CGO: libstdc++ is required

The 08-wasm module's BLS verifier (`blsverifier.CustomQuerier()`, needed by the
Ethereum light client to check sync committee signatures) links against
`github.com/herumi/bls-eth-go-binary`, which needs `libstdc++` at link time.

On a machine with `g++` installed this is automatic. Where only the runtime
library is present (`libstdc++.so.6` but no `libstdc++.so` development
symlink), the link fails with:

```
/usr/bin/ld: cannot find -lstdc++: No such file or directory
```

Either install `g++`/`libstdc++-dev`, or point the linker at a symlink you
create yourself:

```bash
mkdir -p ~/.local/lib
ln -sf /usr/lib/x86_64-linux-gnu/libstdc++.so.6 ~/.local/lib/libstdc++.so
CGO_LDFLAGS="-L$HOME/.local/lib" go build ./...
```

wasmvm also links dynamically, so `libwasmvm` must be reachable at runtime.

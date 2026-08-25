package security

import (
	"bytes"
	"go/ast"
	"go/parser"
	"go/printer"
	"go/token"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// This file pins an architectural invariant of this chain: no Go code on the
// live path performs ICS23 merkle proof verification.
//
// That is a property worth holding on its own — proof verification for the
// Ethereum path belongs to the wasm light client, and a Go implementation
// silently appearing alongside it would mean two verifiers with different
// bugs. It also happens to be the reason an unresolved defect in an upstream
// dependency does not apply to this chain. Details of that defect are withheld
// here pending coordinated disclosure with its maintainers, and are not
// required to understand or maintain the assertions below.
//
// The practical point for a future maintainer: if you are here because this
// test failed, do not simply update it to match the new wiring. Registering a
// light client that verifies ICS23 proofs in Go changes this chain's exposure,
// and that change needs to be assessed rather than rubber-stamped.

// ics23ImportPath is the Go implementation of ICS23 proof verification.
const ics23ImportPath = "github.com/cosmos/ics23/go"

// wasmLightClientPath is the only light client this chain routes.
const wasmLightClientPath = "github.com/cosmos/ibc-go/modules/light-clients/08-wasm/v11"

// TestICS23ProofVerificationNotReachableOnThisChain asserts the two premises
// that keep Go-side ICS23 proof verification off this chain's live path:
//
//	(a) 08-wasm is the only client route registered, so no other light client's
//	    VerifyMembership can be dispatched to; and
//	(b) 08-wasm never calls ics23 — it forwards opaque proof bytes to the wasm
//	    contract — so no ICS23 structure is decoded in Go.
//
// The ics23 package *is* linked into the binary: it is a transitive dependency
// of the SDK store layer. Linkage is not reachability, and this test is what
// keeps that distinction honest as the wiring changes.
func TestICS23ProofVerificationNotReachableOnThisChain(t *testing.T) {
	t.Run("only the 08-wasm client route is registered", func(t *testing.T) {
		routes := clientRoutesRegisteredIn(t, filepath.Join("..", "app", "app.go"))

		if len(routes) != 1 {
			t.Fatalf("expected exactly one ClientKeeper.AddRoute call, found %d: %v\n\n"+
				"A light client route has been added or removed. If the new client verifies "+
				"ICS23 proofs in Go — 07-tendermint being the common case — this chain's "+
				"exposure has changed and must be re-assessed, not merely re-baselined.",
				len(routes), routes)
		}
		if got := routes[0]; got != "ibcwasmtypes.ModuleName" {
			t.Fatalf("the registered client route is %q, not the expected 08-wasm route.\n\n"+
				"See the note above: the invariant this file documents no longer holds.", got)
		}
	})

	t.Run("08-wasm does not call ics23", func(t *testing.T) {
		dir := moduleDir(t, wasmLightClientPath)

		if files := goFilesImporting(t, dir, ics23ImportPath); len(files) != 0 {
			t.Fatalf("08-wasm now imports %s in: %v\n\n"+
				"It previously forwarded opaque proof bytes to the wasm contract and never "+
				"touched ics23. If it now verifies ICS23 proofs in Go, this chain has gained "+
				"a second proof verifier and the invariant this file documents no longer holds.",
				ics23ImportPath, files)
		}
	})

	t.Run("no in-tree code calls ics23", func(t *testing.T) {
		// The security package itself imports ics23 — that is the fuzz harness.
		// Everything else must not.
		files := goFilesImporting(t, "..", ics23ImportPath)
		for _, f := range files {
			if filepath.Base(filepath.Dir(f)) == "security" {
				continue
			}
			t.Errorf("%s imports %s outside the fuzz harness; this chain's proof "+
				"verification path has changed and must be re-assessed", f, ics23ImportPath)
		}
	})
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

// clientRoutesRegisteredIn parses the given file and returns the client-type
// argument of every `…ClientKeeper.AddRoute(…)` call, as written in source.
//
// Parsing the AST rather than grepping means a renamed import or a reformatted
// call site does not silently defeat the check.
func clientRoutesRegisteredIn(t *testing.T, path string) []string {
	t.Helper()

	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, path, nil, 0)
	if err != nil {
		t.Fatalf("parse %s: %v", path, err)
	}

	render := func(n ast.Node) string {
		var buf bytes.Buffer
		if err := printer.Fprint(&buf, fset, n); err != nil {
			t.Fatalf("render node: %v", err)
		}
		return buf.String()
	}

	var routes []string
	ast.Inspect(file, func(n ast.Node) bool {
		call, ok := n.(*ast.CallExpr)
		if !ok {
			return true
		}
		sel, ok := call.Fun.(*ast.SelectorExpr)
		if !ok || sel.Sel.Name != "AddRoute" || len(call.Args) == 0 {
			return true
		}
		// Distinguish the client router from the IBC application routers,
		// which also expose AddRoute.
		if !strings.HasSuffix(render(sel.X), "ClientKeeper") {
			return true
		}
		routes = append(routes, render(call.Args[0]))
		return true
	})
	return routes
}

// goFilesImporting returns the non-test .go files under dir that import the
// given path.
func goFilesImporting(t *testing.T, dir, importPath string) []string {
	t.Helper()

	var found []string
	quoted := `"` + importPath + `"`

	err := filepath.Walk(dir, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() {
			if name := info.Name(); name == "testdata" || name == "node_modules" || name == ".git" {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}

		fset := token.NewFileSet()
		parsed, err := parser.ParseFile(fset, path, nil, parser.ImportsOnly)
		if err != nil {
			// Unparseable files are not our concern here; the build catches them.
			return nil //nolint:nilerr
		}
		for _, imp := range parsed.Imports {
			if imp.Path.Value == quoted {
				found = append(found, path)
				break
			}
		}
		return nil
	})
	if err != nil {
		t.Fatalf("walk %s: %v", dir, err)
	}
	return found
}

// moduleDir resolves a module path to its directory in the module cache.
func moduleDir(t *testing.T, modulePath string) string {
	t.Helper()

	out, err := exec.Command("go", "list", "-m", "-f", "{{.Dir}}", modulePath).Output()
	if err != nil {
		t.Fatalf("go list -m %s: %v", modulePath, err)
	}
	dir := strings.TrimSpace(string(out))
	if dir == "" {
		t.Fatalf("go list -m %s returned no directory", modulePath)
	}
	return dir
}

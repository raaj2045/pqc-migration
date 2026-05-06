module crypto_micro

go 1.23.5

require (
	github.com/cloudflare/circl v1.6.1
	github.com/cosmos/cosmos-sdk v0.53.0
)

require (
	cosmossdk.io/api v0.9.2 // indirect
	cosmossdk.io/collections v1.2.1 // indirect
	cosmossdk.io/core v0.11.3 // indirect
	cosmossdk.io/errors v1.0.2 // indirect
	cosmossdk.io/math v1.5.3 // indirect
	cosmossdk.io/schema v1.1.0 // indirect
	cosmossdk.io/x/tx v0.14.0 // indirect
	github.com/cometbft/cometbft v1.0.1 // indirect
	github.com/cometbft/cometbft/api v1.0.0 // indirect
	github.com/cosmos/cosmos-proto v1.0.0-beta.5 // indirect
	github.com/cosmos/gogoproto v1.7.0 // indirect
	github.com/davecgh/go-spew v1.1.2-0.20180830191138-d8f796af33cc // indirect
	github.com/decred/dcrd/dcrec/secp256k1/v4 v4.4.0 // indirect
	github.com/go-kit/log v0.2.1 // indirect
	github.com/go-logfmt/logfmt v0.6.0 // indirect
	github.com/golang/protobuf v1.5.4 // indirect
	github.com/google/go-cmp v0.7.0 // indirect
	github.com/iancoleman/strcase v0.3.0 // indirect
	github.com/oasisprotocol/curve25519-voi v0.0.0-20230904125328-1f23a7beb09a // indirect
	github.com/petermattis/goid v0.0.0-20240813172612-4fcff4a6cae7 // indirect
	github.com/pkg/errors v0.9.1 // indirect
	github.com/pmezard/go-difflib v1.0.1-0.20181226105442-5d4384ee4fb2 // indirect
	github.com/sasha-s/go-deadlock v0.3.5 // indirect
	github.com/stretchr/testify v1.10.0 // indirect
	github.com/supranational/blst v0.3.13 // indirect
	github.com/tendermint/go-amino v0.16.0 // indirect
	github.com/tidwall/btree v1.7.0 // indirect
	golang.org/x/crypto v0.38.0 // indirect
	golang.org/x/net v0.40.0 // indirect
	golang.org/x/sys v0.33.0 // indirect
	golang.org/x/text v0.25.0 // indirect
	google.golang.org/genproto/googleapis/api v0.0.0-20250324211829-b45e905df463 // indirect
	google.golang.org/genproto/googleapis/rpc v0.0.0-20250422160041-2d3770c4ea7f // indirect
	google.golang.org/grpc v1.72.1 // indirect
	google.golang.org/protobuf v1.36.6 // indirect
	gopkg.in/yaml.v3 v3.0.1 // indirect
	sigs.k8s.io/yaml v1.4.0 // indirect
)

replace (
	cosmossdk.io/api => ../../cosmos/api
	cosmossdk.io/core => ../../cosmos/core
	cosmossdk.io/store => ../../cosmos/store
	cosmossdk.io/x/tx => ../../cosmos/x/tx
	github.com/cosmos/cosmos-sdk => ../../cosmos
)

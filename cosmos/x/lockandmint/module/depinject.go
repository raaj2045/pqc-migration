package module

import (
	"cosmossdk.io/core/appmodule"
	"cosmossdk.io/core/store"
	"cosmossdk.io/depinject"
	"cosmossdk.io/log"

	"github.com/cosmos/cosmos-sdk/codec"
	authtypes "github.com/cosmos/cosmos-sdk/x/auth/types"
	govtypes "github.com/cosmos/cosmos-sdk/x/gov/types"

	"github.com/cosmos/cosmos-sdk/x/lockandmint/keeper"

	// Use the generated API package
	modulev1 "cosmossdk.io/api/cosmos/lockandmint/module/v1"
)

var _ appmodule.AppModule = AppModule{}

func init() {
	appmodule.Register(
		&modulev1.Module{}, // Use the generated type
		appmodule.Provide(ProvideModule),
	)
}

type ModuleInputs struct {
	depinject.In

	Cdc          codec.Codec
	StoreService store.KVStoreService
	Logger       log.Logger
	Config       *modulev1.Module // Use the generated type
}

type ModuleOutputs struct {
	depinject.Out

	LockAndMintKeeper keeper.Keeper
	Module            appmodule.AppModule
}

func ProvideModule(in ModuleInputs) ModuleOutputs {
	// Default authority to governance module account
	authority := authtypes.NewModuleAddress(govtypes.ModuleName)
	if in.Config.Authority != "" {
		authority = authtypes.NewModuleAddressOrBech32Address(in.Config.Authority)
	}

	k := keeper.NewKeeper(
		in.Cdc,
		in.StoreService,
		in.Logger,
		authority.String(),
	)

	m := NewAppModule(
		in.Cdc,
		k,
	)

	return ModuleOutputs{
		LockAndMintKeeper: k,
		Module:            m,
	}
}

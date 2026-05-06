package keeper

import (
	"context"

	"github.com/cosmos/cosmos-sdk/x/lockandmint/types"
)

// InitGenesis initializes the module's state from a provided genesis state
func (k Keeper) InitGenesis(ctx context.Context, genState types.GenesisState) {
	// Store all accounts from genesis
	for _, account := range genState.Accounts {
		k.SetUserAccount(ctx, *account)
	}
}

// ExportGenesis returns the module's exported genesis
func (k Keeper) ExportGenesis(ctx context.Context) *types.GenesisState {
	genesis := types.DefaultGenesis()

	// Iterate through all stored accounts and add to genesis
	store := k.storeService.OpenKVStore(ctx)
	iterator, err := store.Iterator([]byte(types.UserAccountPrefix), nil)
	if err != nil {
		panic(err)
	}
	defer iterator.Close()

	for ; iterator.Valid(); iterator.Next() {
		var account types.UserAccount
		k.cdc.MustUnmarshal(iterator.Value(), &account)
		genesis.Accounts = append(genesis.Accounts, &account)
	}

	return genesis
}

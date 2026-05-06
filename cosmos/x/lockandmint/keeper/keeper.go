package keeper

import (
	"context"

	"cosmossdk.io/core/store"
	"cosmossdk.io/log"
	"github.com/cosmos/cosmos-sdk/codec"

	"github.com/cosmos/cosmos-sdk/x/lockandmint/types"
)

type Keeper struct {
	cdc          codec.BinaryCodec
	storeService store.KVStoreService
	logger       log.Logger
	authority    string // Authority for admin functions
}

func NewKeeper(
	cdc codec.BinaryCodec,
	storeService store.KVStoreService,
	logger log.Logger,
	authority string,
) Keeper {
	return Keeper{
		cdc:          cdc,
		storeService: storeService,
		logger:       logger,
		authority:    authority,
	}
}

// GetAuthority returns the module's authority
func (k Keeper) GetAuthority() string {
	return k.authority
}

// SetUserAccount stores a user account
func (k Keeper) SetUserAccount(ctx context.Context, account types.UserAccount) {
	store := k.storeService.OpenKVStore(ctx)
	bz := k.cdc.MustMarshal(&account)
	store.Set(types.UserAccountKey(account.Address), bz)
}

// GetUserAccount retrieves a user account
func (k Keeper) GetUserAccount(ctx context.Context, address string) (types.UserAccount, bool) {
	store := k.storeService.OpenKVStore(ctx)
	bz, err := store.Get(types.UserAccountKey(address))
	if err != nil || bz == nil {
		return types.UserAccount{}, false
	}

	var account types.UserAccount
	k.cdc.MustUnmarshal(bz, &account)
	return account, true
}

// GetUserAccountOrCreate retrieves account or creates new one with zero balances
func (k Keeper) GetUserAccountOrCreate(ctx context.Context, address string) types.UserAccount {
	account, found := k.GetUserAccount(ctx, address)
	if !found {
		account = types.UserAccount{
			Address:       address,
			Balance:       "0",
			LockedBalance: "0",
		}
	}
	return account
}

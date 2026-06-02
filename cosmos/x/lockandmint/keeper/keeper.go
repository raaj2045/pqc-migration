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

// SetParams stores the module parameters
func (k Keeper) SetParams(ctx context.Context, params types.Params) {
	store := k.storeService.OpenKVStore(ctx)
	bz := k.cdc.MustMarshal(&params)
	store.Set(types.ParamsKey(), bz)
}

// GetParams retrieves the module parameters. Returns zero-value Params if unset.
func (k Keeper) GetParams(ctx context.Context) (types.Params, error) {
	store := k.storeService.OpenKVStore(ctx)
	bz, err := store.Get(types.ParamsKey())
	if err != nil {
		return types.Params{}, err
	}
	if bz == nil {
		return types.Params{}, nil
	}

	var params types.Params
	k.cdc.MustUnmarshal(bz, &params)
	return params, nil
}

// HasProcessedEvent reports whether a source lock-event id has already been minted
func (k Keeper) HasProcessedEvent(ctx context.Context, eventID string) bool {
	store := k.storeService.OpenKVStore(ctx)
	bz, err := store.Get(types.ProcessedEventKey(eventID))
	if err != nil || bz == nil {
		return false
	}
	return true
}

// SetProcessedEvent marks a source lock-event id as minted (replay protection)
func (k Keeper) SetProcessedEvent(ctx context.Context, eventID string) {
	store := k.storeService.OpenKVStore(ctx)
	store.Set(types.ProcessedEventKey(eventID), []byte{1})
}

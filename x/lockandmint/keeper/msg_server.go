package keeper

import (
	"context"

	"cosmossdk.io/math"
	sdk "github.com/cosmos/cosmos-sdk/types"

	"github.com/raaj2045/pqchain-v2/x/lockandmint/types"
)

type msgServer struct {
	Keeper
}

// NewMsgServerImpl returns an implementation of the MsgServer interface
func NewMsgServerImpl(keeper Keeper) types.MsgServer {
	return &msgServer{Keeper: keeper}
}

var _ types.MsgServer = msgServer{}

// Lock handles MsgLock - locks amount from user's balance
func (ms msgServer) Lock(goCtx context.Context, msg *types.MsgLock) (*types.MsgLockResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	// Parse amount
	amount, ok := math.NewIntFromString(msg.Amount)
	if !ok || amount.IsNegative() {
		return nil, types.ErrInvalidAmount
	}

	// Get user account
	account := ms.GetUserAccountOrCreate(ctx, msg.UserAddress)

	// Parse current balance
	currentBalance, ok := math.NewIntFromString(account.Balance)
	if !ok {
		currentBalance = math.ZeroInt()
	}

	// Check sufficient balance
	if currentBalance.LT(amount) {
		return nil, types.ErrInsufficientBalance
	}

	// Parse current locked balance
	currentLocked, ok := math.NewIntFromString(account.LockedBalance)
	if !ok {
		currentLocked = math.ZeroInt()
	}

	// Update balances
	newBalance := currentBalance.Sub(amount)
	newLocked := currentLocked.Add(amount)

	account.Balance = newBalance.String()
	account.LockedBalance = newLocked.String()

	// Save updated account
	ms.SetUserAccount(ctx, account)

	// Emit event
	ctx.EventManager().EmitEvent(
		sdk.NewEvent(
			"lock",
			sdk.NewAttribute("user", msg.UserAddress),
			sdk.NewAttribute("amount", msg.Amount),
		),
	)

	return &types.MsgLockResponse{}, nil
}

// Mint handles MsgMint - mints tokens to receiver's balance
//
// NOTE (Phase 3 port): the bridge_authority gate that existed in the
// original module has intentionally NOT been ported here. Mint is
// currently open to any signer; only replay protection (event_id) and
// amount validation are enforced. A proof-verification gate is planned
// for a later phase — do not treat this as production-ready as-is.
func (ms msgServer) Mint(goCtx context.Context, msg *types.MsgMint) (*types.MsgMintResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	// Parse amount
	amount, ok := math.NewIntFromString(msg.Amount)
	if !ok || amount.IsNegative() {
		return nil, types.ErrInvalidAmount
	}

	// Replay protection: every mint must carry a unique source lock-event id,
	// and each id may be minted at most once.
	if msg.EventId == "" {
		return nil, types.ErrMissingEventID
	}
	if ms.HasProcessedEvent(ctx, msg.EventId) {
		return nil, types.ErrEventAlreadyProcessed
	}
	ms.SetProcessedEvent(ctx, msg.EventId)

	// Get receiver account
	account := ms.GetUserAccountOrCreate(ctx, msg.Receiver)

	// Parse current balance
	currentBalance, ok := math.NewIntFromString(account.Balance)
	if !ok {
		currentBalance = math.ZeroInt()
	}

	// Add minted amount to balance
	newBalance := currentBalance.Add(amount)
	account.Balance = newBalance.String()

	// Save updated account
	ms.SetUserAccount(ctx, account)

	// Emit event
	ctx.EventManager().EmitEvent(
		sdk.NewEvent(
			"mint",
			sdk.NewAttribute("receiver", msg.Receiver),
			sdk.NewAttribute("amount", msg.Amount),
		),
	)

	return &types.MsgMintResponse{}, nil
}

// SetBalance handles MsgSetBalance - admin function to set user balance
func (ms msgServer) SetBalance(goCtx context.Context, msg *types.MsgSetBalance) (*types.MsgSetBalanceResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	// Parse amount
	amount, ok := math.NewIntFromString(msg.Amount)
	if !ok || amount.IsNegative() {
		return nil, types.ErrInvalidAmount
	}

	// Get user account
	account := ms.GetUserAccountOrCreate(ctx, msg.UserAddress)

	// Set new balance
	account.Balance = amount.String()

	// Save updated account
	ms.SetUserAccount(ctx, account)

	// Emit event
	ctx.EventManager().EmitEvent(
		sdk.NewEvent(
			"set_balance",
			sdk.NewAttribute("user", msg.UserAddress),
			sdk.NewAttribute("amount", msg.Amount),
		),
	)

	return &types.MsgSetBalanceResponse{}, nil
}

// UpdateParams handles MsgUpdateParams - gov-gated update of module parameters
// (e.g. rotating the bridge authority). The signer must be the module's gov
// authority, which is distinct from the bridge authority it sets.
func (ms msgServer) UpdateParams(goCtx context.Context, msg *types.MsgUpdateParams) (*types.MsgUpdateParamsResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	// Only the module's gov authority may update params
	if ms.GetAuthority() != msg.Authority {
		return nil, types.ErrUnauthorized
	}

	params := types.Params{}
	if msg.Params != nil {
		params = *msg.Params
	}
	ms.SetParams(ctx, params)

	return &types.MsgUpdateParamsResponse{}, nil
}

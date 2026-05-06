package keeper

import (
	"context"

	"cosmossdk.io/math"
	sdk "github.com/cosmos/cosmos-sdk/types"

	"github.com/cosmos/cosmos-sdk/x/lockandmint/types"
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
func (ms msgServer) Mint(goCtx context.Context, msg *types.MsgMint) (*types.MsgMintResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	// Check authority
	// if ms.GetAuthority() != msg.Authority {
	// 	return nil, types.ErrUnauthorized
	// }

	// Parse amount
	amount, ok := math.NewIntFromString(msg.Amount)
	if !ok || amount.IsNegative() {
		return nil, types.ErrInvalidAmount
	}

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

	// Check authority
	// if ms.GetAuthority() != msg.Authority {
	// 	return nil, types.ErrUnauthorized
	// }

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

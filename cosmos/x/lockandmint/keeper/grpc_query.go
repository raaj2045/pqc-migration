package keeper

import (
	"context"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	"github.com/cosmos/cosmos-sdk/x/lockandmint/types"
)

type queryServer struct {
	Keeper
}

// NewQueryServerImpl returns an implementation of the QueryServer interface
func NewQueryServerImpl(keeper Keeper) types.QueryServer {
	return &queryServer{Keeper: keeper}
}

var _ types.QueryServer = queryServer{}

// GetAccountDetails returns complete account information
func (qs queryServer) GetAccountDetails(goCtx context.Context, req *types.QueryAccountDetailsRequest) (*types.QueryAccountDetailsResponse, error) {
	if req == nil {
		return nil, status.Error(codes.InvalidArgument, "invalid request")
	}

	if req.Address == "" {
		return nil, status.Error(codes.InvalidArgument, "address cannot be empty")
	}

	account, found := qs.GetUserAccount(goCtx, req.Address)
	if !found {
		return nil, status.Error(codes.NotFound, "account not found")
	}

	return &types.QueryAccountDetailsResponse{
		Account: &account,
	}, nil
}

// GetBalance returns only the balance of an account
func (qs queryServer) GetBalance(goCtx context.Context, req *types.QueryBalanceRequest) (*types.QueryBalanceResponse, error) {
	if req == nil {
		return nil, status.Error(codes.InvalidArgument, "invalid request")
	}

	if req.Address == "" {
		return nil, status.Error(codes.InvalidArgument, "address cannot be empty")
	}

	account := qs.GetUserAccountOrCreate(goCtx, req.Address)

	return &types.QueryBalanceResponse{
		Balance: account.Balance,
	}, nil
}

// GetLockedBalance returns only the locked balance of an account
func (qs queryServer) GetLockedBalance(goCtx context.Context, req *types.QueryLockedBalanceRequest) (*types.QueryLockedBalanceResponse, error) {
	if req == nil {
		return nil, status.Error(codes.InvalidArgument, "invalid request")
	}

	if req.Address == "" {
		return nil, status.Error(codes.InvalidArgument, "address cannot be empty")
	}

	account := qs.GetUserAccountOrCreate(goCtx, req.Address)

	return &types.QueryLockedBalanceResponse{
		LockedBalance: account.LockedBalance,
	}, nil
}

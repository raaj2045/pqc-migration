package types

import "cosmossdk.io/errors"

var (
	ErrInsufficientBalance = errors.Register(ModuleName, 1, "insufficient balance")
	ErrAccountNotFound     = errors.Register(ModuleName, 2, "account not found")
	ErrInvalidAmount       = errors.Register(ModuleName, 3, "invalid amount")
	ErrUnauthorized        = errors.Register(ModuleName, 4, "unauthorized")
)

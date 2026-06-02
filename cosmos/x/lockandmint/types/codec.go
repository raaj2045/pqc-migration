package types

import (
	"github.com/cosmos/cosmos-sdk/codec"
	codectypes "github.com/cosmos/cosmos-sdk/codec/types"
	sdk "github.com/cosmos/cosmos-sdk/types"
	"github.com/cosmos/cosmos-sdk/types/msgservice"
)

func RegisterCodec(cdc *codec.LegacyAmino) {
	cdc.RegisterConcrete(&MsgLock{}, "lockandmint/MsgLock", nil)
	cdc.RegisterConcrete(&MsgMint{}, "lockandmint/MsgMint", nil)
	cdc.RegisterConcrete(&MsgSetBalance{}, "lockandmint/MsgSetBalance", nil)
	cdc.RegisterConcrete(&MsgUpdateParams{}, "lockandmint/MsgUpdateParams", nil)
}

func RegisterInterfaces(registry codectypes.InterfaceRegistry) {
	registry.RegisterImplementations((*sdk.Msg)(nil),
		&MsgLock{},
		&MsgMint{},
		&MsgSetBalance{},
		&MsgUpdateParams{},
	)
	msgservice.RegisterMsgServiceDesc(registry, &_Msg_serviceDesc)
}

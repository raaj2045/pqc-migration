package cli

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/cosmos/cosmos-sdk/client"
	"github.com/cosmos/cosmos-sdk/client/flags"
	"github.com/cosmos/cosmos-sdk/client/tx"

	"github.com/cosmos/cosmos-sdk/x/lockandmint/types"
)

// GetTxCmd returns the transaction commands for this module
func GetTxCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:                        types.ModuleName,
		Short:                      "lockandmint transaction subcommands",
		DisableFlagParsing:         true,
		SuggestionsMinimumDistance: 2,
		RunE:                       client.ValidateCmd,
	}

	cmd.AddCommand(
		CmdLock(),
		CmdMint(),
		CmdSetBalance(),
		CmdUpdateParams(),
	)

	return cmd
}

func CmdLock() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "lock [amount]",
		Short: "Lock tokens",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			clientCtx, err := client.GetClientTxContext(cmd)
			if err != nil {
				return err
			}

			msg := &types.MsgLock{
				UserAddress: clientCtx.GetFromAddress().String(),
				Amount:      args[0],
			}

			return tx.GenerateOrBroadcastTxCLI(clientCtx, cmd.Flags(), msg)
		},
	}

	flags.AddTxFlagsToCmd(cmd)
	return cmd
}

func CmdMint() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "mint [receiver] [amount] --event-id <id>",
		Short: "Mint tokens against a unique originating lock event",
		Long: `Mint tokens to a receiver's balance.

The --event-id identifies the originating Ethereum lock event (typically
txHash:logIndex from the relayer). It is required for all mint operations:
the chain rejects mints with a missing or already-processed event id, which
provides replay / double-mint protection.`,
		Args: cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			clientCtx, err := client.GetClientTxContext(cmd)
			if err != nil {
				return err
			}

			eventID, err := cmd.Flags().GetString("event-id")
			if err != nil {
				return err
			}
			if eventID == "" {
				return fmt.Errorf("event id is required; pass --event-id <value> (e.g. the originating lock event's txHash:logIndex)")
			}

			msg := &types.MsgMint{
				Authority: clientCtx.GetFromAddress().String(),
				Receiver:  args[0],
				Amount:    args[1],
				EventId:   eventID,
			}

			return tx.GenerateOrBroadcastTxCLI(clientCtx, cmd.Flags(), msg)
		},
	}

	cmd.Flags().String("event-id", "", "unique originating lock-event id (e.g. txHash:logIndex); required")
	flags.AddTxFlagsToCmd(cmd)
	return cmd
}

func CmdSetBalance() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "set-balance [user-address] [amount]",
		Short: "Set user balance",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			clientCtx, err := client.GetClientTxContext(cmd)
			if err != nil {
				return err
			}

			msg := &types.MsgSetBalance{
				Authority:   clientCtx.GetFromAddress().String(),
				UserAddress: args[0],
				Amount:      args[1],
			}

			return tx.GenerateOrBroadcastTxCLI(clientCtx, cmd.Flags(), msg)
		},
	}

	flags.AddTxFlagsToCmd(cmd)
	return cmd
}

func CmdUpdateParams() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "update-params [bridge-authority]",
		Short: "Submit a (gov-gated) MsgUpdateParams to set the bridge authority",
		Long: `Update the lockandmint module params (currently: bridge_authority).

On production chains, MsgUpdateParams must be wrapped in a governance
proposal because the module's authority is the gov module account. The
standard flow is:
  1. Construct a JSON proposal file containing a MsgUpdateParams message
     with authority set to the gov module account.
  2. Submit via 'simd tx gov submit-proposal <file>'.
  3. Deposit and vote.

This command (--from <key>) submits MsgUpdateParams directly and will be
rejected with ErrUnauthorized unless the signing key matches the module's
gov authority. It is provided primarily for testing or for chains that
have explicitly configured a non-gov authority.`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			clientCtx, err := client.GetClientTxContext(cmd)
			if err != nil {
				return err
			}

			msg := &types.MsgUpdateParams{
				Authority: clientCtx.GetFromAddress().String(),
				Params: &types.Params{
					BridgeAuthority: args[0],
				},
			}

			return tx.GenerateOrBroadcastTxCLI(clientCtx, cmd.Flags(), msg)
		},
	}

	flags.AddTxFlagsToCmd(cmd)
	return cmd
}

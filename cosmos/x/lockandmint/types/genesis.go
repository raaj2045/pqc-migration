package types

// DefaultGenesis returns the default genesis state
func DefaultGenesis() *GenesisState {
	return &GenesisState{
		Accounts: []*UserAccount{},
	}
}

// Validate performs basic genesis state validation returning an error upon any failure
func (gs GenesisState) Validate() error {
	// Check for duplicate addresses
	addressMap := make(map[string]bool)

	for _, account := range gs.Accounts {
		if account.Address == "" {
			return ErrInvalidAmount.Wrap("address cannot be empty")
		}

		if addressMap[account.Address] {
			return ErrInvalidAmount.Wrapf("duplicate account address: %s", account.Address)
		}
		addressMap[account.Address] = true
	}

	return nil
}

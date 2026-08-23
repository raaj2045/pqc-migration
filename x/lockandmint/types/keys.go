package types

const (
	ModuleName           = "lockandmint"
	StoreKey             = ModuleName
	UserAccountPrefix    = "user_account"
	ProcessedEventPrefix = "processed_event"
	ParamsPrefix         = "params"
)

func UserAccountKey(address string) []byte {
	return []byte(UserAccountPrefix + address)
}

func ProcessedEventKey(eventID string) []byte {
	return []byte(ProcessedEventPrefix + eventID)
}

func ParamsKey() []byte {
	return []byte(ParamsPrefix)
}

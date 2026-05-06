package types

const (
	ModuleName        = "lockandmint"
	StoreKey          = ModuleName
	UserAccountPrefix = "user_account"
)

func UserAccountKey(address string) []byte {
	return []byte(UserAccountPrefix + address)
}

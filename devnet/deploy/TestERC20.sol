// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.28;

import { ERC20 } from "@openzeppelin-contracts/token/ERC20/ERC20.sol";

/// @title TestERC20
/// @notice Ethereum-native test asset for the reverse (EVM-escrow /
/// Cosmos-voucher) ICS-20 direction: escrowed by ICS20Transfer.sendTransfer
/// rather than minted as a voucher, because it is never registered as an
/// IBCERC20 (see ICS20Transfer._sendTransferFromEscrowWithSender). Devnet
/// only: mint is unrestricted.
///
/// Mirrors solidity-ibc-eureka's own test/solidity-ibc/mocks/TestERC20.sol,
/// which serves the identical role in that repo's test suite. Vendored here
/// (see devnet/deploy/README.md) so the deployment is preserved with the
/// code that depends on it, rather than living only in the external,
/// gitignored eureka checkout.
contract TestERC20 is ERC20 {
    constructor() ERC20("Test ERC20", "TERC") { }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}

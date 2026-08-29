// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.28;

// Phase 4a local deployment: IBC Eureka core wired to a local PQChain devnet.
// Modeled on E2ETestDeploy.s.sol but without ICS27/IFT.
//
// This script deliberately does NOT create the SP1ICS07Tendermint light client.
// The client's four program verification keys must be derived from the SP1
// program ELFs (proof-api computes them via SP1Program::get_vkey), and its
// trusted state must come from a live light block. Constructing the client here
// forced both to be faked -- vkeys were bytes32(0) and the trusted state came
// from COSMOS_* env vars -- which only works against SP1MockVerifier.
//
// Client creation therefore moves to a proof-api CreateClient call, which
// returns deployment calldata carrying the real vkeys and live trusted state.
// The caller broadcasts that calldata and then registers the resulting address
// with ICS26Router.addClient. See devnet/README.md.
//
// Both verifiers are deployed: verifierGroth16 for real proving, verifierMock
// for fast iteration. Which one is used is decided at client creation, by the
// address passed as proof-api's `sp1Verifier` parameter.

// solhint-disable custom-errors,gas-custom-errors

import { stdJson } from "forge-std/StdJson.sol";
import { Script } from "forge-std/Script.sol";
import { console } from "forge-std/console.sol";

import { ICS26Router } from "../contracts/ICS26Router.sol";
import { ICS20Transfer } from "../contracts/ICS20Transfer.sol";
import { ICS20Lib } from "../contracts/utils/ICS20Lib.sol";
import { ERC1967Proxy } from "@openzeppelin-contracts/proxy/ERC1967/ERC1967Proxy.sol";
import { DeployAccessManagerWithRoles } from "./deployments/DeployAccessManagerWithRoles.sol";
import { IBCERC20 } from "../contracts/utils/IBCERC20.sol";
import { Escrow } from "../contracts/utils/Escrow.sol";
import { SP1Verifier as SP1VerifierGroth16 } from "@sp1-contracts/v6.1.0/SP1VerifierGroth16.sol";
import { SP1MockVerifier } from "@sp1-contracts/SP1MockVerifier.sol";
import { AccessManager } from "@openzeppelin-contracts/access/manager/AccessManager.sol";

contract Phase4aDeploy is Script, DeployAccessManagerWithRoles {
    function run() public {
        vm.startBroadcast();

        // 1. SP1 verifiers. Groth16 is the real one (circuit v6.1.0, matching
        //    the artifacts under ~/.sp1/circuits/groth16/v6.1.0). The mock is
        //    kept so the devnet can still be driven without a prover.
        SP1VerifierGroth16 verifierGroth16 = new SP1VerifierGroth16();
        SP1MockVerifier verifierMock = new SP1MockVerifier();

        // 2. AccessManager with deployer as initial admin
        AccessManager accessManager = new AccessManager(msg.sender);

        // 3. ICS26Router (logic + ERC1967 proxy)
        address routerLogic = address(new ICS26Router());
        address ics26Router =
            address(new ERC1967Proxy(routerLogic, abi.encodeCall(ICS26Router.initialize, (address(accessManager)))));

        // 4. ICS20Transfer (logic + proxy, with Escrow and IBCERC20 beacons)
        address transferLogic = address(new ICS20Transfer());
        address ics20Transfer = address(
            new ERC1967Proxy(
                transferLogic,
                abi.encodeCall(
                    ICS20Transfer.initialize,
                    (ics26Router, address(new Escrow()), address(new IBCERC20()), address(0), address(accessManager))
                )
            )
        );

        // 5. Roles: relayer selectors public, deployer as id/erc20 customizer
        accessManagerSetTargetRoles(accessManager, ics26Router, ics20Transfer, address(0), true);
        accessManagerSetRoles(
            accessManager, new address[](0), new address[](0), new address[](0), msg.sender, msg.sender, msg.sender
        );

        // 6. Register the transfer app on the router
        ICS26Router(ics26Router).addIBCApp(ICS20Lib.DEFAULT_PORT_ID, ics20Transfer);

        vm.stopBroadcast();

        // The light client and its router registration are created afterwards,
        // from proof-api CreateClient calldata. See the note at the top.
        string memory json = "out";
        json.serialize("verifierGroth16", vm.toString(address(verifierGroth16)));
        json.serialize("verifierMock", vm.toString(address(verifierMock)));
        json.serialize("accessManager", vm.toString(address(accessManager)));
        json.serialize("ics26Router", vm.toString(ics26Router));
        string memory finalJson = json.serialize("ics20Transfer", vm.toString(ics20Transfer));
        console.log("PHASE4A_RESULT %s", finalJson);
    }

    using stdJson for string;
}

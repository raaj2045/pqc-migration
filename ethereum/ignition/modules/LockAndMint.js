import { buildModule } from "@nomicfoundation/hardhat-ignition/modules";

const LockAndMintModule = buildModule("LockAndMintModule", (m) => {
  const lockAndMint = m.contract("LockAndMint");
  return { lockAndMint };
});

export default LockAndMintModule;
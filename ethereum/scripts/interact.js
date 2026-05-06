import hre from "hardhat";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

async function main() {
  const __filename = fileURLToPath(import.meta.url);
  const __dirname = path.dirname(__filename);
  
  const chainId = "31337";
  const deploymentPath = path.join(__dirname, "..", "ignition", "deployments", `chain-${chainId}`, "deployed_addresses.json");
  
  let contractAddress;
  
  try {
    const deployedAddresses = JSON.parse(fs.readFileSync(deploymentPath, "utf8"));
    contractAddress = deployedAddresses["LockAndMintModule#LockAndMint"];
    
    if (!contractAddress) {
      throw new Error("Contract address not found in deployment artifacts");
    }
  } catch (error) {
    console.error("❌ Could not read deployment address from:", deploymentPath);
    console.error("Make sure you've deployed the contract first with:");
    console.error("npx hardhat ignition deploy ignition/modules/LockAndMint.ts --network localhost");
    return;
  }
  
  const [owner, user] = await hre.ethers.getSigners();
  const LockAndMint = await hre.ethers.getContractFactory("LockAndMint");
  const lockAndMint = LockAndMint.attach(contractAddress);
  
  console.log("🚀 Interacting with LockAndMint contract at:", contractAddress);
  console.log("Owner:", owner.address);
  console.log("User:", user.address);
  
  console.log("\n1. Setting user balance to 1000...");
  await lockAndMint.setBalance(user.address, 1000);
  console.log("✅ Balance set");
  
  const balance = await lockAndMint.getBalance(user.address);
  console.log("User balance:", balance.toString());
  
  console.log("\n2. Minting 500 tokens to user...");
  await lockAndMint.mint(user.address, 500);
  console.log("✅ Tokens minted");
  
  const newBalance = await lockAndMint.getBalance(user.address);
  console.log("New user balance:", newBalance.toString());
  
  console.log("\n3. Locking 100 tokens to cosmos address...");
  const cosmosAddress = "cosmos1h8ts30zguyx7pn42rtzx0yg3mqqxeexl2k9vwh";
  
  const tx = await lockAndMint.connect(user).lock(100, cosmosAddress);
  const receipt = await tx.wait();
  console.log("✅ Tokens locked");
  
  const event = receipt.logs.find((log) => {
    try {
      const parsed = lockAndMint.interface.parseLog(log);
      return parsed?.name === "TokensLocked";
    } catch {
      return false;
    }
  });
  
  if (event) {
    const parsedEvent = lockAndMint.interface.parseLog(event);
    console.log("🎉 TokensLocked Event:");
    console.log("  Owner:", parsedEvent?.args[0]);
    console.log("  Cosmos Receiver:", parsedEvent?.args[1]);
    console.log("  Amount:", parsedEvent?.args[2].toString());
    console.log("  Timestamp:", parsedEvent?.args[3].toString());
  }
  
  console.log("\n4. Final account details:");
  const details = await lockAndMint.getAccountDetails(user.address);
  console.log("  Balance:", details.balance.toString());
  console.log("  Locked Balance:", details.lockedBalance.toString());
  console.log("  Total Balance:", details.totalBalance.toString());
  
  console.log("\n🎉 All interactions completed successfully!");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
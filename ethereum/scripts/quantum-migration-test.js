import hre from "hardhat";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

async function main() {
  const __filename = fileURLToPath(import.meta.url);
  const __dirname = path.dirname(__filename);
  
  const deploymentPath = path.join(__dirname, "..", "ignition", "deployments", "chain-31337", "deployed_addresses.json");
  const deployedAddresses = JSON.parse(fs.readFileSync(deploymentPath, "utf8"));
  const contractAddress = deployedAddresses["LockAndMintModule#LockAndMint"];
  
  const [owner, user] = await hre.ethers.getSigners();
  const LockAndMint = await hre.ethers.getContractFactory("LockAndMint");
  const lockAndMint = LockAndMint.attach(contractAddress);
  
  console.log("🔮 QUANTUM-SAFE MIGRATION TEST");
  console.log("=" .repeat(50));
  
  // Test different quantum-safe addresses
  const quantumAddresses = [
    "cosmos1quantum1safemigrationtest1234567890abcdef", // Mock quantum address 1
    "cosmos1quantum2safemigrationtest1234567890abcdef", // Mock quantum address 2
    "cosmos1quantum3safemigrationtest1234567890abcdef", // Mock quantum address 3
  ];
  
  const migrationAmounts = [1000, 2500, 5000]; // Different migration amounts
  
  // Set up initial balance for migration testing
  console.log("\n1. Setting up Ethereum balance for migration...");
  await lockAndMint.setBalance(user.address, 10000);
  console.log("✅ Set 10,000 tokens for migration testing");
  
  // Test multiple quantum-safe migrations
  for (let i = 0; i < quantumAddresses.length; i++) {
    console.log(`\n${i + 2}. Migrating ${migrationAmounts[i]} tokens to quantum-safe address ${i + 1}`);
    console.log(`   Target: ${quantumAddresses[i]}`);
    
    const tx = await lockAndMint.connect(user).lock(migrationAmounts[i], quantumAddresses[i]);
    const receipt = await tx.wait();
    
    // Parse migration event
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
      console.log("✅ Migration Event Emitted:");
      console.log(`   📡 From (Classical): ${parsedEvent?.args[0]}`);
      console.log(`   🔐 To (Quantum-Safe): ${parsedEvent?.args[1]}`);
      console.log(`   💰 Amount: ${parsedEvent?.args[2].toString()}`);
      console.log(`   ⏰ Timestamp: ${parsedEvent?.args[3].toString()}`);
    }
    
    // Wait a bit between migrations
    await new Promise(resolve => setTimeout(resolve, 2000));
  }
  
  // Check final Ethereum state
  console.log("\n🔍 Final Ethereum State:");
  const details = await lockAndMint.getAccountDetails(user.address);
  console.log(`   Available Balance: ${details.balance.toString()}`);
  console.log(`   Locked (Migrated): ${details.lockedBalance.toString()}`);
  console.log(`   Total: ${details.totalBalance.toString()}`);
  
  console.log("\n🎯 Migration Summary:");
  console.log(`   Total Migrated: ${migrationAmounts.reduce((a, b) => a + b, 0)} tokens`);
  console.log(`   Quantum-Safe Recipients: ${quantumAddresses.length}`);
  console.log(`   Migration Transactions: ${quantumAddresses.length}`);
  
  console.log("\n✨ Quantum-safe migration test completed!");
  console.log("   Check relayer logs and Cosmos balances to verify successful migration");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
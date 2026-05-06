import hre from "hardhat";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

async function performanceTest() {
  console.log("⚡ MIGRATION PERFORMANCE TEST");
  console.log("=" .repeat(50));
  
  const __filename = fileURLToPath(import.meta.url);
  const __dirname = path.dirname(__filename);
  
  const deploymentPath = path.join(__dirname, "..", "ignition", "deployments", "chain-31337", "deployed_addresses.json");
  const deployedAddresses = JSON.parse(fs.readFileSync(deploymentPath, "utf8"));
  const contractAddress = deployedAddresses["LockAndMintModule#LockAndMint"];
  
  const [owner, user] = await hre.ethers.getSigners();
  const LockAndMint = await hre.ethers.getContractFactory("LockAndMint");
  const lockAndMint = LockAndMint.attach(contractAddress);
  
  // Set up large balance for testing
  await lockAndMint.setBalance(user.address, 100000);
  
  const testCases = [
    { description: "Small Migration", amount: 100, recipients: 1 },
    { description: "Medium Migration", amount: 1000, recipients: 5 },
    { description: "Large Migration", amount: 5000, recipients: 10 },
    { description: "Bulk Migration", amount: 500, recipients: 20 },
  ];
  
  for (const testCase of testCases) {
    console.log(`\n📊 ${testCase.description}:`);
    console.log(`   Amount per migration: ${testCase.amount}`);
    console.log(`   Number of recipients: ${testCase.recipients}`);
    
    const startTime = Date.now();
    
    for (let i = 0; i < testCase.recipients; i++) {
      const quantumAddress = `cosmos1quantum${i.toString().padStart(10, '0')}migration${Date.now()}`;
      await lockAndMint.connect(user).lock(testCase.amount, quantumAddress);
    }
    
    const endTime = Date.now();
    const duration = endTime - startTime;
    
    console.log(`   ⏱️  Duration: ${duration}ms`);
    console.log(`   📈 Avg per migration: ${(duration / testCase.recipients).toFixed(2)}ms`);
    console.log(`   💰 Total migrated: ${testCase.amount * testCase.recipients}`);
  }
  
  const finalDetails = await lockAndMint.getAccountDetails(user.address);
  console.log(`\n🏁 Final State:`);
  console.log(`   Total migrated: ${finalDetails.lockedBalance.toString()}`);
  console.log(`   Remaining balance: ${finalDetails.balance.toString()}`);
}

performanceTest().catch(console.error);
const { exec } = require("child_process");
const { promisify } = require("util");
const execAsync = promisify(exec);

async function verifyQuantumMigration() {
  console.log("🔍 END-TO-END QUANTUM MIGRATION VERIFICATION");
  console.log("=" .repeat(60));
  
  const cosmosPath = process.env.COSMOS_CLI_PATH?.replace('/build/simd', '') || '../cosmos';
  
  // Create quantum-safe address for testing
  console.log("\n1. Creating fresh quantum-safe address...");
  const keyName = `quantum-test-${Date.now()}`;
  
  try {
    await execAsync(`cd ${cosmosPath} && ./build/simd keys add ${keyName} --algo mldsa44 --keyring-backend test`);
    
    const quantumAddr = await execAsync(`cd ${cosmosPath} && ./build/simd keys show ${keyName} -a --keyring-backend test`);
    const cleanAddr = quantumAddr.stdout.trim();
    
    console.log(`✅ Created quantum-safe address: ${cleanAddr}`);
    
    // Check initial balance
    console.log("\n2. Checking initial Cosmos balance...");
    const initialBalance = await execAsync(`cd ${cosmosPath} && ./build/simd query lockandmint balance ${cleanAddr}`);
    console.log(`Initial balance: ${initialBalance.stdout.trim()}`);
    
    console.log("\n3. ⚠️  Now trigger Ethereum migration to this address:");
    console.log(`   Quantum-safe address: ${cleanAddr}`);
    console.log(`   Use this address in your Ethereum lock transaction`);
    
    // Wait for user to trigger migration
    console.log("\n4. Waiting 30 seconds for migration to process...");
    await new Promise(resolve => setTimeout(resolve, 30000));
    
    // Check final balance
    console.log("\n5. Checking post-migration Cosmos balance...");
    const finalBalance = await execAsync(`cd ${cosmosPath} && ./build/simd query lockandmint balance ${cleanAddr}`);
    console.log(`Final balance: ${finalBalance.stdout.trim()}`);
    
    // Check account details
    const accountDetails = await execAsync(`cd ${cosmosPath} && ./build/simd query lockandmint account-details ${cleanAddr}`);
    console.log("\n6. Complete account details:");
    console.log(accountDetails.stdout);
    
    console.log("\n✅ Quantum migration verification completed!");
    
  } catch (error) {
    console.error("❌ Verification failed:", error);
  }
}

verifyQuantumMigration();
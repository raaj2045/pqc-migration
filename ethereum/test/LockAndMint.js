import { expect } from "chai";
import hre from "hardhat";

describe("LockAndMint", function () {
  let lockAndMint;
  let owner;
  let user;
  let otherAccount;

  beforeEach(async function () {
    [owner, user, otherAccount] = await hre.ethers.getSigners();
    
    const LockAndMint = await hre.ethers.getContractFactory("LockAndMint");
    lockAndMint = await LockAndMint.deploy();
  });

  describe("Deployment", function () {
    it("Should set the right owner", async function () {
      expect(await lockAndMint.owner()).to.equal(owner.address);
    });

    it("Should initialize with zero balances", async function () {
      expect(await lockAndMint.getBalance(user.address)).to.equal(0);
      expect(await lockAndMint.getLockedBalance(user.address)).to.equal(0);
    });
  });

  describe("Minting", function () {
    it("Should mint tokens to user", async function () {
      await lockAndMint.mint(user.address, 1000);
      expect(await lockAndMint.getBalance(user.address)).to.equal(1000);
    });

    it("Should emit TokensMinted event", async function () {
      await expect(lockAndMint.mint(user.address, 1000))
        .to.emit(lockAndMint, "TokensMinted")
        .withArgs(user.address, 1000, await hre.ethers.provider.getBlock("latest").then(b => b?.timestamp + 1));
    });

    it("Should only allow owner to mint", async function () {
      await expect(lockAndMint.connect(user).mint(user.address, 1000))
        .to.be.revertedWithCustomError(lockAndMint, "OwnableUnauthorizedAccount");
    });

    it("Should reject minting to zero address", async function () {
      await expect(lockAndMint.mint(hre.ethers.ZeroAddress, 1000))
        .to.be.revertedWith("Invalid receiver address");
    });

    it("Should reject minting zero amount", async function () {
      await expect(lockAndMint.mint(user.address, 0))
        .to.be.revertedWith("Amount must be greater than 0");
    });

    it("Should accumulate minted tokens", async function () {
      await lockAndMint.mint(user.address, 500);
      await lockAndMint.mint(user.address, 300);
      expect(await lockAndMint.getBalance(user.address)).to.equal(800);
    });
  });

  describe("Balance Setting", function () {
    it("Should set user balance", async function () {
      await lockAndMint.setBalance(user.address, 500);
      expect(await lockAndMint.getBalance(user.address)).to.equal(500);
    });

    it("Should emit BalanceSet event", async function () {
      await expect(lockAndMint.setBalance(user.address, 500))
        .to.emit(lockAndMint, "BalanceSet")
        .withArgs(user.address, 500, await hre.ethers.provider.getBlock("latest").then(b => b?.timestamp + 1));
    });

    it("Should only allow owner to set balance", async function () {
      await expect(lockAndMint.connect(user).setBalance(user.address, 500))
        .to.be.revertedWithCustomError(lockAndMint, "OwnableUnauthorizedAccount");
    });

    it("Should reject setting balance for zero address", async function () {
      await expect(lockAndMint.setBalance(hre.ethers.ZeroAddress, 500))
        .to.be.revertedWith("Invalid user address");
    });

    it("Should overwrite existing balance", async function () {
      await lockAndMint.setBalance(user.address, 1000);
      await lockAndMint.setBalance(user.address, 500);
      expect(await lockAndMint.getBalance(user.address)).to.equal(500);
    });
  });

  describe("Locking", function () {
    beforeEach(async function () {
      // Set initial balance for user
      await lockAndMint.setBalance(user.address, 1000);
    });

    it("Should lock tokens and emit event with cosmos address", async function () {
      const cosmosAddress = "cosmos1h8ts30zguyx7pn42rtzx0yg3mqqxeexl2k9vwh";
      
      await expect(lockAndMint.connect(user).lock(100, cosmosAddress))
        .to.emit(lockAndMint, "TokensLocked")
        .withArgs(user.address, cosmosAddress, 100, await hre.ethers.provider.getBlock("latest").then(b => b?.timestamp + 1));
      
      // Check balances
      expect(await lockAndMint.getBalance(user.address)).to.equal(900);
      expect(await lockAndMint.getLockedBalance(user.address)).to.equal(100);
    });

    it("Should revert if insufficient balance", async function () {
      const cosmosAddress = "cosmos1h8ts30zguyx7pn42rtzx0yg3mqqxeexl2k9vwh";
      
      await expect(lockAndMint.connect(user).lock(1500, cosmosAddress))
        .to.be.revertedWith("Insufficient balance");
    });

    it("Should revert if locking zero amount", async function () {
      const cosmosAddress = "cosmos1h8ts30zguyx7pn42rtzx0yg3mqqxeexl2k9vwh";
      
      await expect(lockAndMint.connect(user).lock(0, cosmosAddress))
        .to.be.revertedWith("Amount must be greater than 0");
    });

    it("Should revert if cosmos receiver is empty", async function () {
      await expect(lockAndMint.connect(user).lock(100, ""))
        .to.be.revertedWith("Cosmos receiver cannot be empty");
    });

    it("Should allow multiple lock operations", async function () {
      const cosmosAddress = "cosmos1h8ts30zguyx7pn42rtzx0yg3mqqxeexl2k9vwh";
      
      await lockAndMint.connect(user).lock(300, cosmosAddress);
      await lockAndMint.connect(user).lock(200, cosmosAddress);
      
      expect(await lockAndMint.getBalance(user.address)).to.equal(500);
      expect(await lockAndMint.getLockedBalance(user.address)).to.equal(500);
    });

    it("Should lock exact balance amount", async function () {
      const cosmosAddress = "cosmos1h8ts30zguyx7pn42rtzx0yg3mqqxeexl2k9vwh";
      
      await lockAndMint.connect(user).lock(1000, cosmosAddress);
      
      expect(await lockAndMint.getBalance(user.address)).to.equal(0);
      expect(await lockAndMint.getLockedBalance(user.address)).to.equal(1000);
    });
  });

  describe("Account Details", function () {
    it("Should return correct account details for new account", async function () {
      const details = await lockAndMint.getAccountDetails(user.address);
      expect(details.balance).to.equal(0);
      expect(details.lockedBalance).to.equal(0);
      expect(details.totalBalance).to.equal(0);
    });

    it("Should return correct account details after setting balance", async function () {
      await lockAndMint.setBalance(user.address, 1000);
      
      const details = await lockAndMint.getAccountDetails(user.address);
      expect(details.balance).to.equal(1000);
      expect(details.lockedBalance).to.equal(0);
      expect(details.totalBalance).to.equal(1000);
    });

    it("Should return correct account details after locking", async function () {
      await lockAndMint.setBalance(user.address, 1000);
      await lockAndMint.connect(user).lock(200, "cosmos1test");
      
      const details = await lockAndMint.getAccountDetails(user.address);
      expect(details.balance).to.equal(800);
      expect(details.lockedBalance).to.equal(200);
      expect(details.totalBalance).to.equal(1000);
    });

    it("Should return correct account details after minting and locking", async function () {
      await lockAndMint.mint(user.address, 500);
      await lockAndMint.setBalance(user.address, 1000);
      await lockAndMint.connect(user).lock(300, "cosmos1test");
      
      const details = await lockAndMint.getAccountDetails(user.address);
      expect(details.balance).to.equal(700);
      expect(details.lockedBalance).to.equal(300);
      expect(details.totalBalance).to.equal(1000);
    });
  });

  describe("Complex Scenarios", function () {
    it("Should handle complete workflow", async function () {
      const cosmosAddress = "cosmos1h8ts30zguyx7pn42rtzx0yg3mqqxeexl2k9vwh";
      
      // Set initial balance
      await lockAndMint.setBalance(user.address, 1000);
      
      // Mint additional tokens
      await lockAndMint.mint(user.address, 500);
      expect(await lockAndMint.getBalance(user.address)).to.equal(1500);
      
      // Lock some tokens
      await lockAndMint.connect(user).lock(100, cosmosAddress);
      
      // Final state check
      const details = await lockAndMint.getAccountDetails(user.address);
      expect(details.balance).to.equal(1400);
      expect(details.lockedBalance).to.equal(100);
      expect(details.totalBalance).to.equal(1500);
    });

    it("Should handle multiple users independently", async function () {
      const cosmosAddress = "cosmos1test";
      
      // Setup user1
      await lockAndMint.setBalance(user.address, 1000);
      await lockAndMint.connect(user).lock(200, cosmosAddress);
      
      // Setup user2
      await lockAndMint.setBalance(otherAccount.address, 500);
      await lockAndMint.connect(otherAccount).lock(100, cosmosAddress);
      
      // Check user1
      const user1Details = await lockAndMint.getAccountDetails(user.address);
      expect(user1Details.balance).to.equal(800);
      expect(user1Details.lockedBalance).to.equal(200);
      
      // Check user2
      const user2Details = await lockAndMint.getAccountDetails(otherAccount.address);
      expect(user2Details.balance).to.equal(400);
      expect(user2Details.lockedBalance).to.equal(100);
    });
  });

  describe("Edge Cases", function () {
    it("Should handle zero balances correctly", async function () {
      const cosmosAddress = "cosmos1test";
      
      await expect(lockAndMint.connect(user).lock(1, cosmosAddress))
        .to.be.revertedWith("Insufficient balance");
    });

    it("Should handle very large amounts", async function () {
      const largeAmount = hre.ethers.parseEther("1000000");
      
      await lockAndMint.setBalance(user.address, largeAmount);
      expect(await lockAndMint.getBalance(user.address)).to.equal(largeAmount);
      
      await lockAndMint.connect(user).lock(hre.ethers.parseEther("500000"), "cosmos1test");
      expect(await lockAndMint.getBalance(user.address)).to.equal(hre.ethers.parseEther("500000"));
      expect(await lockAndMint.getLockedBalance(user.address)).to.equal(hre.ethers.parseEther("500000"));
    });

    it("Should handle different cosmos address formats", async function () {
      await lockAndMint.setBalance(user.address, 1000);
      
      const addresses = [
        "cosmos1h8ts30zguyx7pn42rtzx0yg3mqqxeexl2k9vwh",
        "cosmos1abc123def456ghi789jkl012mno345pqr678st",
        "cosmos1test"
      ];
      
      for (let i = 0; i < addresses.length; i++) {
        await expect(lockAndMint.connect(user).lock(100, addresses[i]))
          .to.emit(lockAndMint, "TokensLocked")
          .withArgs(user.address, addresses[i], 100, await hre.ethers.provider.getBlock("latest").then(b => b?.timestamp + 1));
      }
    });
  });
});
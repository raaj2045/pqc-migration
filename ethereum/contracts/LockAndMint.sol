// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

contract LockAndMint is Ownable, ReentrancyGuard {
    // State variables
    mapping(address => uint256) public balances;
    mapping(address => uint256) public lockedBalances;
    
    // Events
    event TokensLocked(
        address indexed owner,
        string indexed cosmosReceiver, // Cosmos address
        uint256 amount,
        uint256 timestamp
    );
    
    event TokensMinted(
        address indexed receiver,
        uint256 amount,
        uint256 timestamp
    );
    
    event BalanceSet(
        address indexed user,
        uint256 amount,
        uint256 timestamp
    );
    
    // Constructor
    constructor() Ownable(msg.sender) {}
    
    // Mint tokens to a user's balance
    function mint(address receiver, uint256 amount) external onlyOwner {
        require(receiver != address(0), "Invalid receiver address");
        require(amount > 0, "Amount must be greater than 0");
        
        balances[receiver] += amount;
        
        emit TokensMinted(receiver, amount, block.timestamp);
    }
    
    // Set user balance (admin function)
    function setBalance(address user, uint256 amount) external onlyOwner {
        require(user != address(0), "Invalid user address");
        
        balances[user] = amount;
        
        emit BalanceSet(user, amount, block.timestamp);
    }
    
    // Lock tokens and emit event with cosmos receiver address
    function lock(uint256 amount, string calldata cosmosReceiver) external nonReentrant {
        require(amount > 0, "Amount must be greater than 0");
        require(bytes(cosmosReceiver).length > 0, "Cosmos receiver cannot be empty");
        require(balances[msg.sender] >= amount, "Insufficient balance");
        
        // Update balances
        balances[msg.sender] -= amount;
        lockedBalances[msg.sender] += amount;
        
        // Emit event with cosmos receiver address
        emit TokensLocked(msg.sender, cosmosReceiver, amount, block.timestamp);
    }
    
    // Get user's balance
    function getBalance(address user) external view returns (uint256) {
        return balances[user];
    }
    
    // Get user's locked balance
    function getLockedBalance(address user) external view returns (uint256) {
        return lockedBalances[user];
    }
    
    // Get user's account details
    function getAccountDetails(address user) external view returns (
        uint256 balance,
        uint256 lockedBalance,
        uint256 totalBalance
    ) {
        balance = balances[user];
        lockedBalance = lockedBalances[user];
        totalBalance = balance + lockedBalance;
    }
}
const hre = require("hardhat");

async function main() {
  if (hre.network.name !== "localhost") {
    throw new Error("Refusing deployment outside the explicitly configured local Hardhat node.");
  }
  const chainId = Number((await hre.ethers.provider.getNetwork()).chainId);
  if (chainId !== 31337) {
    throw new Error(`Refusing deployment: expected local chain ID 31337, received ${chainId}.`);
  }

  const [deployer] = await hre.ethers.getSigners();
  if (!deployer) throw new Error("The local Hardhat node did not provide an unlocked signer.");
  const ledger = await hre.ethers.deployContract("RightsLedger", [deployer.address]);
  await ledger.waitForDeployment();

  const transaction = ledger.deploymentTransaction();
  console.log(`Network: hardhat-local (chain ${chainId})`);
  console.log(`Contract: ${await ledger.getAddress()}`);
  console.log(`Owner: ${deployer.address}`);
  if (transaction) console.log(`Deployment transaction: ${transaction.hash}`);
}

main().catch((error) => {
  console.error(error.message || "Local RightsLedger deployment failed.");
  process.exitCode = 1;
});

const { expect } = require("chai");
const { anyValue } = require("@nomicfoundation/hardhat-chai-matchers/withArgs");
const { ethers } = require("hardhat");

describe("RightsLedger", function () {
  async function deployLedger() {
    const [owner, stranger] = await ethers.getSigners();
    const ledger = await ethers.deployContract("RightsLedger", [owner.address]);
    await ledger.waitForDeployment();
    return { ledger, owner, stranger };
  }

  const recordId = 17n;
  const recordHash = ethers.keccak256(ethers.toUtf8Bytes("canonical-rights-record"));
  const modifiedHash = ethers.keccak256(ethers.toUtf8Bytes("modified-rights-record"));
  const policyVersion = "1.0.0";

  it("records the ID, hash, policy version, timestamp, sender, and event", async function () {
    const { ledger, owner } = await deployLedger();
    await expect(ledger.addRecord(recordId, recordHash, policyVersion))
      .to.emit(ledger, "RightsRecordAnchored")
      .withArgs(recordId, recordHash, policyVersion, anyValue, owner.address);

    const stored = await ledger.getRecord(recordId);
    expect(stored.recordHash).to.equal(recordHash);
    expect(stored.policyVersion).to.equal(policyVersion);
    expect(stored.recordedAt).to.be.greaterThan(0n);
    expect(stored.recordedBy).to.equal(owner.address);
  });

  it("rejects duplicate record identifiers and leaves the first record unchanged", async function () {
    const { ledger } = await deployLedger();
    await ledger.addRecord(recordId, recordHash, policyVersion);
    await expect(ledger.addRecord(recordId, modifiedHash, "2.0.0"))
      .to.be.revertedWithCustomError(ledger, "DuplicateRecord")
      .withArgs(recordId);
    expect((await ledger.getRecord(recordId)).recordHash).to.equal(recordHash);
  });

  it("rejects writes by an unauthorized account", async function () {
    const { ledger, stranger } = await deployLedger();
    await expect(ledger.connect(stranger).addRecord(recordId, recordHash, policyVersion))
      .to.be.revertedWithCustomError(ledger, "OwnableUnauthorizedAccount")
      .withArgs(stranger.address);
  });

  it("retrieves records by ID and distinguishes missing IDs", async function () {
    const { ledger } = await deployLedger();
    await ledger.addRecord(recordId, recordHash, policyVersion);
    expect(await ledger.containsRecord(recordId)).to.equal(true);
    expect(await ledger.containsRecord(999n)).to.equal(false);
    await expect(ledger.getRecord(999n))
      .to.be.revertedWithCustomError(ledger, "RecordNotFound")
      .withArgs(999n);
  });

  it("verifies a stored hash and detects a modified candidate hash", async function () {
    const { ledger } = await deployLedger();
    await ledger.addRecord(recordId, recordHash, policyVersion);
    expect(await ledger.verifyRecord(recordId, recordHash)).to.equal(true);
    expect(await ledger.verifyRecord(recordId, modifiedHash)).to.equal(false);
    expect(await ledger.verifyRecord(999n, recordHash)).to.equal(false);
  });

  it("rejects zero IDs, empty versions, and zero hashes", async function () {
    const { ledger } = await deployLedger();
    await expect(ledger.addRecord(0n, recordHash, policyVersion))
      .to.be.revertedWithCustomError(ledger, "InvalidRecordId");
    await expect(ledger.addRecord(recordId, ethers.ZeroHash, policyVersion))
      .to.be.revertedWithCustomError(ledger, "InvalidRecordHash");
    await expect(ledger.addRecord(recordId, recordHash, ""))
      .to.be.revertedWithCustomError(ledger, "EmptyPolicyVersion");
  });
});

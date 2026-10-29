// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

/// @notice Stores hashes and provenance metadata for signed off-chain rights records.
/// @dev Does not validate signatures, rights, ownership, or the truth of any claim.
contract RightsLedger is Ownable {
    struct RightsRecord {
        bytes32 recordHash;
        string policyVersion;
        uint64 recordedAt;
        address recordedBy;
    }

    mapping(uint256 recordId => RightsRecord record) private _records;
    mapping(uint256 recordId => bool exists) private _exists;

    error InvalidRecordId();
    error InvalidRecordHash();
    error EmptyPolicyVersion();
    error DuplicateRecord(uint256 recordId);
    error RecordNotFound(uint256 recordId);

    event RightsRecordAnchored(
        uint256 indexed recordId,
        bytes32 indexed recordHash,
        string policyVersion,
        uint64 recordedAt,
        address indexed recordedBy
    );

    constructor(address initialOwner) Ownable(initialOwner) {}

    /// @notice Anchors the digest of a canonical rights record. Only the owner can append.
    function addRecord(uint256 recordId, bytes32 recordHash, string calldata policyVersion)
        external
        onlyOwner
    {
        if (recordId == 0) revert InvalidRecordId();
        if (recordHash == bytes32(0)) revert InvalidRecordHash();
        if (bytes(policyVersion).length == 0) revert EmptyPolicyVersion();
        if (_exists[recordId]) revert DuplicateRecord(recordId);

        uint64 recordedAt = uint64(block.timestamp);
        _exists[recordId] = true;
        _records[recordId] = RightsRecord({
            recordHash: recordHash,
            policyVersion: policyVersion,
            recordedAt: recordedAt,
            recordedBy: msg.sender
        });

        emit RightsRecordAnchored(recordId, recordHash, policyVersion, recordedAt, msg.sender);
    }

    /// @notice Returns the immutable record for an identifier.
    function getRecord(uint256 recordId) external view returns (RightsRecord memory) {
        if (!_exists[recordId]) revert RecordNotFound(recordId);
        return _records[recordId];
    }

    function containsRecord(uint256 recordId) external view returns (bool) {
        return _exists[recordId];
    }

    /// @notice Compares a caller-supplied digest with the immutable anchored digest.
    function verifyRecord(uint256 recordId, bytes32 candidateHash) external view returns (bool) {
        return _exists[recordId] && _records[recordId].recordHash == candidateHash;
    }
}

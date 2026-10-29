"""Local-only Web3 adapter for the append-only RightsLedger contract."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class BlockchainError(RuntimeError):
    """Base class for sanitized local-chain integration errors."""


class BlockchainConfigurationError(BlockchainError):
    pass


class BlockchainUnavailable(BlockchainError):
    pass


class BlockchainRecordConflict(BlockchainError):
    pass


class BlockchainTransactionPending(BlockchainError):
    def __init__(self, transaction_hash: str):
        super().__init__("The local transaction was submitted but is not yet confirmed.")
        self.transaction_hash = transaction_hash


class BlockchainTransactionFailed(BlockchainError):
    def __init__(self, message: str, transaction_hash: str | None = None, block_number: int | None = None):
        super().__init__(message)
        self.transaction_hash = transaction_hash
        self.block_number = block_number


@dataclass(frozen=True)
class BlockchainConfig:
    rpc_url: str
    chain_id: int
    contract_address: str
    sender_address: str
    network_name: str
    artifact_path: Path


@dataclass(frozen=True)
class ChainRecord:
    record_hash: str
    policy_version: str
    recorded_at: int
    recorded_by: str
    transaction_hash: str | None = None
    block_number: int | None = None


def canonical_rights_record_bytes(record: dict[str, Any], policy_version: str) -> bytes:
    """Canonical hash input; excludes all chain status and transaction fields."""
    record = dict(record)
    payload = record.get("payload")
    if payload is None:
        payload = record["payload_json"]
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    elif isinstance(payload, str):
        payload = json.loads(payload)
    canonical = {
        "record_id": int(record["id"]),
        "dataset_id": int(payload["dataset_id"]),
        "payload": payload,
        "signer_id": str(record["signer_id"]),
        "public_key": str(record["public_key"]),
        "signature": str(record["signature"]),
        "signature_valid": bool(record["signature_valid"]),
        "verification_message": str(record["verification_message"]),
        "created_at": str(record["created_at"]),
        "policy_version": str(policy_version),
    }
    return json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def hash_rights_record(record: dict[str, Any], policy_version: str) -> str:
    return "0x" + hashlib.sha256(canonical_rights_record_bytes(record, policy_version)).hexdigest()


def _environment_settings() -> dict[str, str]:
    settings = {
        name: value.strip()
        for name in (
            "BLOCKCHAIN_RPC_URL",
            "BLOCKCHAIN_CHAIN_ID",
            "BLOCKCHAIN_CONTRACT_ADDRESS",
            "BLOCKCHAIN_SENDER_ADDRESS",
            "BLOCKCHAIN_NETWORK_NAME",
            "BLOCKCHAIN_TX_TIMEOUT",
        )
        if (value := os.environ.get(name))
    }
    env_file = Path(__file__).resolve().parent / ".env"
    try:
        lines = env_file.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        lines = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name.startswith("BLOCKCHAIN_") and name not in settings:
            settings[name] = value.strip().strip("\"'")
    return settings


def load_config() -> BlockchainConfig:
    settings = _environment_settings()
    required = (
        "BLOCKCHAIN_RPC_URL",
        "BLOCKCHAIN_CONTRACT_ADDRESS",
        "BLOCKCHAIN_SENDER_ADDRESS",
    )
    missing = [name for name in required if not settings.get(name)]
    if missing:
        raise BlockchainConfigurationError(
            "Local blockchain configuration is incomplete: " + ", ".join(missing) + "."
        )

    rpc_url = settings["BLOCKCHAIN_RPC_URL"]
    parsed = urlparse(rpc_url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise BlockchainConfigurationError("RightsLedger is restricted to a loopback local RPC endpoint.")
    if settings.get("BLOCKCHAIN_NETWORK_NAME", "hardhat-local") != "hardhat-local":
        raise BlockchainConfigurationError("Only the hardhat-local network is permitted.")
    try:
        chain_id = int(settings.get("BLOCKCHAIN_CHAIN_ID", "31337"))
    except ValueError as error:
        raise BlockchainConfigurationError("BLOCKCHAIN_CHAIN_ID must be an integer.") from error
    if chain_id != 31337:
        raise BlockchainConfigurationError("RightsLedger only permits local Hardhat chain ID 31337.")

    artifact_path = Path(__file__).resolve().parent.parent / "blockchain" / "artifacts" / "contracts" / "RightsLedger.sol" / "RightsLedger.json"
    return BlockchainConfig(
        rpc_url=rpc_url,
        chain_id=chain_id,
        contract_address=settings["BLOCKCHAIN_CONTRACT_ADDRESS"],
        sender_address=settings["BLOCKCHAIN_SENDER_ADDRESS"],
        network_name="hardhat-local",
        artifact_path=artifact_path,
    )


def _connect():
    config = load_config()
    try:
        from web3 import Web3
    except ImportError as error:
        raise BlockchainConfigurationError("Install the Web3 backend dependency to use RightsLedger.") from error

    if not config.artifact_path.is_file():
        raise BlockchainConfigurationError(
            "RightsLedger contract artifacts are missing. Compile the local contract first."
        )
    try:
        artifact = json.loads(config.artifact_path.read_text(encoding="utf-8"))
        web3 = Web3(Web3.HTTPProvider(config.rpc_url, request_kwargs={"timeout": 5}))
        if not web3.is_connected():
            raise BlockchainUnavailable("Local Hardhat node is unavailable.")
        if int(web3.eth.chain_id) != config.chain_id:
            raise BlockchainConfigurationError("Connected node chain ID does not match local Hardhat chain ID 31337.")
        contract_address = Web3.to_checksum_address(config.contract_address)
        sender_address = Web3.to_checksum_address(config.sender_address)
        if not web3.eth.get_code(contract_address):
            raise BlockchainConfigurationError("No RightsLedger contract is deployed at the configured address.")
        if sender_address.lower() not in {address.lower() for address in web3.eth.accounts}:
            raise BlockchainConfigurationError("Configured sender is not an unlocked account on the local Hardhat node.")
        contract = web3.eth.contract(address=contract_address, abi=artifact["abi"])
        owner = contract.functions.owner().call()
        if owner.lower() != sender_address.lower():
            raise BlockchainConfigurationError("Configured sender is not the RightsLedger owner.")
        return config, web3, contract, sender_address
    except BlockchainError:
        raise
    except Exception as error:
        # Web3 exceptions may include node details; callers receive only a safe summary.
        raise BlockchainUnavailable("Could not connect to the configured local RightsLedger.") from error


def _find_record_event(contract, record_id: int) -> tuple[str | None, int | None]:
    try:
        events = contract.events.RightsRecordAnchored().get_logs(
            from_block=0,
            to_block="latest",
            argument_filters={"recordId": record_id},
        )
        if events:
            latest = events[-1]
            return latest["transactionHash"].hex(), int(latest["blockNumber"])
    except Exception:
        pass
    return None, None


def get_chain_record(record_id: int) -> ChainRecord | None:
    _config, _web3, contract, _sender = _connect()
    try:
        if not contract.functions.containsRecord(record_id).call():
            return None
        stored = contract.functions.getRecord(record_id).call()
        transaction_hash, block_number = _find_record_event(contract, record_id)
        return ChainRecord(
            record_hash="0x" + bytes(stored[0]).hex(),
            policy_version=str(stored[1]),
            recorded_at=int(stored[2]),
            recorded_by=str(stored[3]),
            transaction_hash=transaction_hash,
            block_number=block_number,
        )
    except BlockchainError:
        raise
    except Exception as error:
        raise BlockchainUnavailable("Could not retrieve the local on-chain rights record.") from error


def anchor_record(record_id: int, record_hash: str, policy_version: str) -> ChainRecord:
    config, web3, contract, sender = _connect()
    try:
        if contract.functions.containsRecord(record_id).call():
            current = contract.functions.getRecord(record_id).call()
            on_chain_hash = "0x" + bytes(current[0]).hex()
            if on_chain_hash != record_hash or str(current[1]) != policy_version:
                raise BlockchainRecordConflict(
                    "This rights record identifier is already anchored with a different hash or policy version."
                )
            tx_hash, block_number = _find_record_event(contract, record_id)
            return ChainRecord(on_chain_hash, str(current[1]), int(current[2]), str(current[3]), tx_hash, block_number)

        timeout = int(_environment_settings().get("BLOCKCHAIN_TX_TIMEOUT", "20"))
        tx_hash_bytes = contract.functions.addRecord(
            record_id,
            bytes.fromhex(record_hash.removeprefix("0x")),
            policy_version,
        ).transact({"from": sender})
        tx_hash = web3.to_hex(tx_hash_bytes)
        try:
            receipt = web3.eth.wait_for_transaction_receipt(tx_hash_bytes, timeout=timeout)
        except Exception as error:
            from web3.exceptions import TimeExhausted
            if isinstance(error, TimeExhausted):
                raise BlockchainTransactionPending(tx_hash) from error
            raise BlockchainTransactionFailed("The local RightsLedger transaction failed.", tx_hash) from error
        if int(receipt.status) != 1:
            raise BlockchainTransactionFailed("The local RightsLedger transaction reverted.", tx_hash, int(receipt.blockNumber))
        stored = contract.functions.getRecord(record_id).call()
        return ChainRecord(
            record_hash="0x" + bytes(stored[0]).hex(),
            policy_version=str(stored[1]),
            recorded_at=int(stored[2]),
            recorded_by=str(stored[3]),
            transaction_hash=tx_hash,
            block_number=int(receipt.blockNumber),
        )
    except BlockchainError:
        raise
    except Exception as error:
        raise BlockchainUnavailable("Could not submit the record to local Hardhat.") from error

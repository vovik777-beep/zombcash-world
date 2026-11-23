"""Independent read-only SPL mint-account supply verification."""
from __future__ import annotations

import base64
import math
import struct
from datetime import datetime, timezone

from soi.models import Observation

TOKEN_PROGRAM_IDS = {
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
}
MINT_BASE_SIZE = 82
TOKEN_2022_MINT_EXTENSION_NAMES = {
    1: "TransferFeeConfig",
    3: "MintCloseAuthority",
    4: "ConfidentialTransferMint",
    6: "DefaultAccountState",
    9: "NonTransferable",
    10: "InterestBearingConfig",
    12: "PermanentDelegate",
    14: "TransferHook",
    16: "ConfidentialTransferFeeConfig",
    18: "MetadataPointer",
    19: "TokenMetadata",
    20: "GroupPointer",
    21: "TokenGroup",
    22: "GroupMemberPointer",
    23: "TokenGroupMember",
    24: "ConfidentialMintBurn",
    25: "ScaledUiAmount",
    26: "Pausable",
    28: "PermissionedBurn",
}


class MintDecodeError(ValueError):
    pass


def _pubkey_from_bytes(raw: bytes) -> str | None:
    if len(raw) != 32:
        raise MintDecodeError("Public key field must be 32 bytes")
    if not any(raw):
        return None
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    zeros = len(raw) - len(raw.lstrip(b"\0"))
    number = int.from_bytes(raw, "big")
    encoded = ""
    while number:
        number, rem = divmod(number, 58)
        encoded = alphabet[rem] + encoded
    return "1" * zeros + encoded


def _decode_transfer_fee(data: bytes) -> dict:
    if len(data) != 108:
        raise MintDecodeError("Invalid TransferFeeConfig payload length")
    def fee(offset: int) -> dict:
        epoch = int.from_bytes(data[offset:offset + 8], "little")
        maximum_fee = int.from_bytes(data[offset + 8:offset + 16], "little")
        bps = int.from_bytes(data[offset + 16:offset + 18], "little")
        if bps > 10_000:
            raise MintDecodeError("Transfer fee basis points exceed 10000")
        return {"epoch": epoch, "maximum_fee_raw": str(maximum_fee),
                "basis_points": bps, "percent": bps / 100}
    return {
        "config_authority": _pubkey_from_bytes(data[0:32]),
        "withdraw_withheld_authority": _pubkey_from_bytes(data[32:64]),
        "withheld_amount_raw": str(int.from_bytes(data[64:72], "little")),
        "older_transfer_fee": fee(72),
        "newer_transfer_fee": fee(90),
    }


def _decode_extension_payload(extension_type: int, data: bytes) -> dict | None:
    if extension_type == 1:
        return _decode_transfer_fee(data)
    if extension_type == 4:  # ConfidentialTransferMint
        if len(data) != 65 or data[32] not in (0, 1):
            raise MintDecodeError("Invalid ConfidentialTransferMint payload")
        return {
            "confidential_transfer_authority": _pubkey_from_bytes(data[:32]),
            "auto_approve_new_accounts": bool(data[32]),
            "auditor_elgamal_key_present": any(data[33:65]),
        }
    if extension_type == 9:  # NonTransferable
        if data:
            raise MintDecodeError("Invalid NonTransferable payload length")
        return {"non_transferable": True}
    if extension_type == 10:  # InterestBearingConfig
        if len(data) != 52:
            raise MintDecodeError("Invalid InterestBearingConfig payload length")
        return {
            "rate_authority": _pubkey_from_bytes(data[:32]),
            "initialization_timestamp": int.from_bytes(data[32:40], "little", signed=True),
            "pre_update_average_rate_basis_points": int.from_bytes(data[40:42], "little", signed=True),
            "last_update_timestamp": int.from_bytes(data[42:50], "little", signed=True),
            "current_rate_basis_points": int.from_bytes(data[50:52], "little", signed=True),
        }
    if extension_type == 3:
        if len(data) != 32:
            raise MintDecodeError("Invalid MintCloseAuthority payload length")
        return {"close_authority": _pubkey_from_bytes(data)}
    if extension_type == 6:
        if len(data) != 1 or data[0] not in (0, 1, 2):
            raise MintDecodeError("Invalid DefaultAccountState payload")
        return {"default_account_state": {0: "uninitialized", 1: "initialized", 2: "frozen"}[data[0]]}
    if extension_type == 12:
        if len(data) != 32:
            raise MintDecodeError("Invalid PermanentDelegate payload length")
        return {"permanent_delegate": _pubkey_from_bytes(data)}
    if extension_type == 14:
        if len(data) != 64:
            raise MintDecodeError("Invalid TransferHook payload length")
        return {
            "transfer_hook_authority": _pubkey_from_bytes(data[:32]),
            "transfer_hook_program_id": _pubkey_from_bytes(data[32:]),
        }
    if extension_type == 25:  # ScaledUiAmount
        if len(data) != 56:
            raise MintDecodeError("Invalid ScaledUiAmount payload length")
        multiplier = struct.unpack("<d", data[32:40])[0]
        effective_timestamp = struct.unpack("<q", data[40:48])[0]
        new_multiplier = struct.unpack("<d", data[48:56])[0]
        if not math.isfinite(multiplier) or not math.isfinite(new_multiplier):
            raise MintDecodeError("ScaledUiAmount multiplier is not finite")
        return {
            "authority": _pubkey_from_bytes(data[:32]),
            "multiplier": multiplier,
            "new_multiplier_effective_timestamp": effective_timestamp,
            "new_multiplier": new_multiplier,
        }
    if extension_type == 26:
        if len(data) != 33 or data[32] not in (0, 1):
            raise MintDecodeError("Invalid Pausable payload")
        return {"pausable_authority": _pubkey_from_bytes(data[:32]),
                "paused": bool(data[32])}
    return None


def _coption_pubkey(data: bytes, offset: int, field: str) -> str | None:
    tag = int.from_bytes(data[offset:offset + 4], "little")
    if tag == 0:
        return None
    if tag != 1:
        raise MintDecodeError(f"Invalid {field} COption tag")
    raw = data[offset + 4:offset + 36]
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    zeros = len(raw) - len(raw.lstrip(b"\0"))
    number = int.from_bytes(raw, "big")
    encoded = ""
    while number:
        number, rem = divmod(number, 58)
        encoded = alphabet[rem] + encoded
    return "1" * zeros + (encoded or ("" if zeros else "1"))


def _decode_token_2022_mint_extensions(raw: bytes) -> list[dict]:
    """Decode Token-2022 mint TLV entries; preserve unknown extension IDs without guessing."""
    if len(raw) == MINT_BASE_SIZE:
        return []
    # Token-2022 places the AccountType marker after the shared 165-byte
    # account boundary, leaving 83 zero-padding bytes after the 82-byte Mint.
    if len(raw) <= 165:
        if not any(raw[MINT_BASE_SIZE:]):
            return []
        raise MintDecodeError("Truncated Token-2022 mint extension header")
    if any(raw[MINT_BASE_SIZE:165]):
        raise MintDecodeError("Non-zero Token-2022 mint padding before AccountType")
    if raw[165] != 1:  # AccountType::Mint
        if not any(raw[82:]):
            return []
        raise MintDecodeError("Token-2022 extension account-type marker is not Mint")
    extensions = []
    offset = 166
    while offset < len(raw):
        remaining = raw[offset:]
        if not any(remaining):
            break
        if len(remaining) < 4:
            raise MintDecodeError("Truncated Token-2022 mint extension TLV header")
        extension_type = int.from_bytes(raw[offset:offset + 2], "little")
        extension_length = int.from_bytes(raw[offset + 2:offset + 4], "little")
        if extension_type == 0 and extension_length == 0:
            if any(raw[offset:]):
                raise MintDecodeError("Invalid Token-2022 mint extension padding")
            break
        start = offset + 4
        end = start + extension_length
        if end > len(raw):
            raise MintDecodeError("Token-2022 mint extension exceeds account data")
        payload = raw[start:end]
        decoded_payload = _decode_extension_payload(extension_type, payload)
        extension_record = {
            "type_id": extension_type,
            "name": TOKEN_2022_MINT_EXTENSION_NAMES.get(extension_type, "Unknown"),
            "data_length": extension_length,
        }
        if decoded_payload is not None:
            extension_record["decoded"] = decoded_payload
        extensions.append(extension_record)
        offset = end
    return extensions


def decode_mint_account(account: dict) -> dict:
    """Decode the stable 82-byte SPL Mint base layout and Token-2022 TLV headers."""
    if not isinstance(account, dict):
        raise MintDecodeError("Mint account response is missing")
    owner = account.get("owner")
    if owner not in TOKEN_PROGRAM_IDS:
        raise MintDecodeError("Mint account owner is not SPL Token or Token-2022")
    data = account.get("data")
    if not isinstance(data, list) or len(data) < 2 or data[1] != "base64":
        raise MintDecodeError("Mint account data is not base64")
    try:
        raw = base64.b64decode(data[0], validate=True)
    except Exception as exc:
        raise MintDecodeError("Invalid base64 mint account data") from exc
    if len(raw) < MINT_BASE_SIZE:
        raise MintDecodeError("Mint account data is shorter than the SPL base layout")
    decimals = raw[44]
    initialized = raw[45]
    if initialized not in (0, 1):
        raise MintDecodeError("Invalid mint initialized flag")
    mint_extensions = _decode_token_2022_mint_extensions(raw) if owner == "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb" else []
    extension_signals = {extension["name"]: extension.get("decoded", {}) for extension in mint_extensions}
    transfer_fee = extension_signals.get("TransferFeeConfig", {})
    older_fee = transfer_fee.get("older_transfer_fee", {})
    newer_fee = transfer_fee.get("newer_transfer_fee", {})
    return {
        "token_program": owner,
        "mint_authority": _coption_pubkey(raw, 0, "mint authority"),
        "supply_raw": int.from_bytes(raw[36:44], "little"),
        "decimals": decimals,
        "initialized": bool(initialized),
        "freeze_authority": _coption_pubkey(raw, 46, "freeze authority"),
        "account_data_length": len(raw),
        "token_2022_extensions_present": bool(mint_extensions),
        "mint_extensions": mint_extensions,
        "extension_signals": extension_signals,
        "risk_indicators": {
            "mint_authority_present": _coption_pubkey(raw, 0, "mint authority") is not None,
            "freeze_authority_present": _coption_pubkey(raw, 46, "freeze authority") is not None,
            "permanent_delegate_present": bool(extension_signals.get("PermanentDelegate", {}).get("permanent_delegate")),
            "non_transferable": bool(extension_signals.get("NonTransferable", {}).get("non_transferable")),
            "confidential_transfer_enabled": "ConfidentialTransferMint" in extension_signals,
            "scaled_ui_amount_present": "ScaledUiAmount" in extension_signals,
            "interest_bearing_ui_amount": "InterestBearingConfig" in extension_signals,
            "mint_close_authority_present": bool(extension_signals.get("MintCloseAuthority", {}).get("close_authority")),
            "transfer_hook_present": bool(extension_signals.get("TransferHook", {}).get("transfer_hook_program_id")),
            "transfer_fee_config_present": bool(transfer_fee),
            "transfer_fee_configured": bool(
                (older_fee.get("basis_points") or 0) > 0
                or (newer_fee.get("basis_points") or 0) > 0
            ),
            "older_transfer_fee_basis_points": older_fee.get("basis_points"),
            "newer_transfer_fee_basis_points": newer_fee.get("basis_points"),
            "transfer_fee_active_epoch_unknown": bool(transfer_fee),
            "default_account_state": extension_signals.get("DefaultAccountState", {}).get("default_account_state"),
            "paused": extension_signals.get("Pausable", {}).get("paused"),
        },
    }


def _inspect_transfer_hook_program(rpc, program_id: str) -> dict:
    response = rpc.account_info(program_id)
    if not isinstance(response, dict) or not isinstance(response.get("context"), dict):
        raise RuntimeError("Transfer-hook program account response has no context")
    value = response.get("value")
    if value is None:
        return {"status": "account_not_found"}
    result = {
        "status": "account_found",
        "program_id": program_id,
        "owner": value.get("owner"),
        "executable": value.get("executable") is True,
        "context_slot": response["context"].get("slot"),
    }
    if result["executable"] is not True:
        result["status"] = "not_executable"
        return result
    loader = "BPFLoaderUpgradeab1e11111111111111111111111"
    if value.get("owner") != loader:
        result["upgradeability"] = "not_upgradeable_loader_or_unknown_loader"
        return result
    data = value.get("data")
    if not isinstance(data, list) or len(data) < 2 or data[1] != "base64":
        raise MintDecodeError("Transfer-hook program account data is not base64")
    try:
        raw = base64.b64decode(data[0], validate=True)
    except Exception as exc:
        raise MintDecodeError("Invalid transfer-hook program account base64") from exc
    if len(raw) < 36 or int.from_bytes(raw[:4], "little") != 2:
        raise MintDecodeError("Invalid upgradeable-loader Program account state")
    programdata_address = _pubkey_from_bytes(raw[4:36])
    if not programdata_address:
        raise MintDecodeError("Upgradeable program has no ProgramData address")
    programdata_response = rpc.account_info(programdata_address)
    if not isinstance(programdata_response, dict) or not isinstance(programdata_response.get("context"), dict):
        raise RuntimeError("ProgramData account response has no context")
    programdata_value = programdata_response.get("value")
    if not isinstance(programdata_value, dict) or programdata_value.get("owner") != loader:
        raise MintDecodeError("ProgramData account owner does not match upgradeable loader")
    programdata_data = programdata_value.get("data")
    if not isinstance(programdata_data, list) or len(programdata_data) < 2 or programdata_data[1] != "base64":
        raise MintDecodeError("ProgramData account data is not base64")
    programdata_raw = base64.b64decode(programdata_data[0], validate=True)
    if len(programdata_raw) < 13 or int.from_bytes(programdata_raw[:4], "little") != 3:
        raise MintDecodeError("Invalid upgradeable-loader ProgramData state")
    authority_tag = programdata_raw[12]
    if authority_tag not in (0, 1):
        raise MintDecodeError("Invalid ProgramData upgrade-authority option")
    authority = _pubkey_from_bytes(programdata_raw[13:45]) if authority_tag == 1 else None
    if authority_tag == 1 and len(programdata_raw) < 45:
        raise MintDecodeError("Truncated ProgramData upgrade authority")
    result.update({
        "upgradeability": "upgradeable",
        "programdata_address": programdata_address,
        "programdata_context_slot": programdata_response["context"].get("slot"),
        "deployment_slot": int.from_bytes(programdata_raw[4:12], "little"),
        "upgrade_authority": authority,
        "upgrade_authority_present": authority is not None,
    })
    return result


def collect_mint_supply_state(rpc, mint: str, observed_at: str | None = None) -> Observation:
    """Compare base-layout supply from the mint account with getTokenSupply.

    This verifies total mint supply only. It deliberately never estimates circulating supply.
    """
    response = rpc.account_info(mint)
    if not isinstance(response, dict) or not isinstance(response.get("context"), dict):
        raise RuntimeError("getAccountInfo returned no mint context")
    slot = response["context"].get("slot")
    if isinstance(slot, bool) or not isinstance(slot, int) or slot < 0:
        raise MintDecodeError("Mint account response has no valid context slot")
    decoded = decode_mint_account(response.get("value"))
    if not decoded["initialized"]:
        raise MintDecodeError("Mint account is not initialized")
    epoch_reader = getattr(rpc, "epoch_info", None)
    if callable(epoch_reader):
        try:
            epoch_result = epoch_reader() or {}
            current_epoch = epoch_result.get("epoch")
            if isinstance(current_epoch, int) and not isinstance(current_epoch, bool) and current_epoch >= 0:
                decoded["current_epoch"] = current_epoch
                transfer_fee = decoded.get("extension_signals", {}).get("TransferFeeConfig", {})
                older_fee = transfer_fee.get("older_transfer_fee")
                newer_fee = transfer_fee.get("newer_transfer_fee")
                if older_fee and newer_fee:
                    selected = newer_fee if current_epoch >= newer_fee["epoch"] else older_fee
                    decoded["active_transfer_fee"] = {
                        **selected, "selection_basis": "getEpochInfo",
                        "current_epoch": current_epoch,
                    }
        except Exception as exc:
            decoded["epoch_info_error"] = str(exc)[:200]
    hook_program_id = decoded.get("extension_signals", {}).get("TransferHook", {}).get("transfer_hook_program_id")
    if hook_program_id:
        try:
            decoded["transfer_hook_program_state"] = _inspect_transfer_hook_program(rpc, hook_program_id)
        except Exception as exc:
            decoded["transfer_hook_program_state"] = {
                "status": "inspection_error", "program_id": hook_program_id,
                "error": str(exc)[:200],
            }
    hook_state = decoded.get("transfer_hook_program_state") or {}
    decoded.setdefault("risk_indicators", {}).update({
        "transfer_hook_program_executable": hook_state.get("executable"),
        "transfer_hook_upgradeable": hook_state.get("upgradeability") == "upgradeable",
        "transfer_hook_upgrade_authority_present": hook_state.get("upgrade_authority_present"),
    })
    supply_response = rpc.token_supply(mint) or {}
    supply_value = supply_response.get("value") or {}
    rpc_raw = supply_value.get("amount")
    rpc_decimals = supply_value.get("decimals")
    if not isinstance(rpc_raw, str) or not rpc_raw.isdigit():
        raise RuntimeError("getTokenSupply returned an invalid raw amount")
    if isinstance(rpc_decimals, bool) or not isinstance(rpc_decimals, int):
        raise RuntimeError("getTokenSupply returned invalid decimals")
    decoded["get_token_supply_raw"] = int(rpc_raw)
    decoded["get_token_supply_decimals"] = rpc_decimals
    decoded["supply_matches_get_token_supply"] = (
        decoded["supply_raw"] == int(rpc_raw) and decoded["decimals"] == rpc_decimals
    )
    decoded["mint_account_slot"] = slot
    decoded["token_supply_context_slot"] = (supply_response.get("context") or {}).get("slot")
    decoded["mint"] = mint
    decoded["supply_semantics"] = "total_mint_supply"
    decoded["circulating_supply_raw"] = None
    decoded["circulating_supply_status"] = "not_estimated"
    decoded["limitations"] = [
        "Mint supply includes all minted tokens, regardless of lockup, treasury, LP, or burn-account treatment.",
        "Circulating supply requires separately justified exclusions and is not inferred from mint supply.",
        "Recognized Token-2022 extension payloads are decoded only for explicitly supported types; unknown payloads remain opaque.",
    ]
    observed = observed_at or datetime.now(timezone.utc).isoformat()
    return Observation(
        source="solana_rpc_mint_state",
        source_ref=f"{mint}:mint-state:{slot}",
        observed_at=observed,
        event_at=observed,
        kind="mint_supply_state",
        mint=mint,
        payload={"event": decoded},
        confidence="observed",
    )

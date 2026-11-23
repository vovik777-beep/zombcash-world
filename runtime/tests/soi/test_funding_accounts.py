from soi.sources.funding_accounts import (
    SYSTEM_PROGRAM, TOKEN_PROGRAM, TOKEN_2022_PROGRAM,
    classify_account, collect_funding_source_accounts,
)


def test_classifies_system_owned_account_without_identity_claim():
    result = classify_account("SenderA", {"owner": SYSTEM_PROGRAM, "executable": False, "lamports": 99, "space": 0})
    assert result["classification"] == "system_owned_account"
    assert "not proof" in result["interpretation"]


def test_classifies_parsed_token_account_and_separates_authority():
    result = classify_account("TokenAccount", {
        "owner": TOKEN_PROGRAM, "executable": False,
        "data": {"program": "spl-token", "parsed": {"type": "account", "info": {
            "mint": "MintA", "owner": "AuthorityA", "state": "initialized"
        }}}
    })
    assert result["classification"] == "token_program_owned_account"
    assert result["token_authority"] == "AuthorityA"
    assert result["token_mint"] == "MintA"


def test_classifies_executable_and_other_program_accounts():
    executable = classify_account("ProgramA", {"owner": "LoaderProgram", "executable": True})
    other = classify_account("OwnedA", {"owner": "CustomProgram", "executable": False})
    assert executable["classification"] == "executable_program_account"
    assert other["classification"] == "other_program_owned_account"
    assert other["owner_program"] == "CustomProgram"


def test_missing_account_is_unknown():
    result = classify_account("Missing", None)
    assert result["classification"] == "unknown"
    assert result["account_status"] == "not_found_or_unavailable"


def test_source_collection_deduplicates_and_applies_limit():
    class FakeRpc:
        def __init__(self):
            self.addresses = None

        def parsed_accounts(self, addresses):
            self.addresses = addresses
            return [{"owner": SYSTEM_PROGRAM, "executable": False} for _ in addresses]

    rpc = FakeRpc()
    funding = [
        {"transfers": [{"source_wallet": "A"}, {"source_wallet": "B"}]},
        {"transfers": [{"source_wallet": "A"}, {"source_wallet": "C"}]},
    ]
    result = collect_funding_source_accounts(rpc, funding, limit=2)
    assert rpc.addresses == ["A", "B"]
    assert result["unique_sources_seen"] == 3
    assert result["requested"] == 2
    assert result["truncated"] is True
    assert len(result["accounts"]) == 2


def test_token_2022_program_is_classified_as_token_program_owned():
    result = classify_account("Token2022", {"owner": TOKEN_2022_PROGRAM, "executable": False})
    assert result["classification"] == "token_program_owned_account"

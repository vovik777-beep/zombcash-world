from soi.analytics.funding_network import summarize_funding_network


def test_shared_funder_and_multi_recipient_transaction_are_reported_descriptively():
    result = summarize_funding_network([
        {"wallet": "WalletA", "transfers": [
            {"source_wallet": "FunderA", "destination_wallet": "WalletA", "sol": 1.5, "signature": "tx-shared"}]},
        {"wallet": "WalletB", "transfers": [
            {"source_wallet": "FunderA", "destination_wallet": "WalletB", "sol": 2.0, "signature": "tx-shared"}]},
        {"wallet": "WalletC", "transfers": [
            {"source_wallet": "FunderB", "destination_wallet": "WalletC", "sol": 0.5, "signature": "tx-single"}]},
    ])
    assert result["observed_direct_transfer_count"] == 3
    assert result["distinct_direct_source_count"] == 2
    shared = result["sources"][0]
    assert shared["source_wallet"] == "FunderA"
    assert shared["distinct_sampled_destinations"] == 2
    assert shared["total_observed_sol"] == 3.5
    assert len(result["transactions_funding_multiple_sampled_wallets"]) == 1
    assert result["transactions_funding_multiple_sampled_wallets"][0]["signature"] == "tx-shared"
    assert "do not prove common ownership" in result["interpretation"]


def test_empty_funding_sample_is_explicit_and_safe():
    result = summarize_funding_network([])
    assert result["observed_direct_transfer_count"] == 0
    assert result["sources"] == []
    assert result["transactions_funding_multiple_sampled_wallets"] == []

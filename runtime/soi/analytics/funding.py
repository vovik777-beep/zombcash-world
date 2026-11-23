"""Direct incoming native-SOL transfer evidence; not wallet attribution."""
from __future__ import annotations

SYSTEM_PROGRAM = "11111111111111111111111111111111"
LAMPORTS_PER_SOL = 1_000_000_000

def _instructions(tx):
    message = ((tx.get("transaction") or {}).get("message") or {})
    meta = tx.get("meta") or {}
    for ix in message.get("instructions") or []:
        yield ix
    for group in meta.get("innerInstructions") or []:
        for ix in group.get("instructions") or []:
            yield ix

def extract_incoming_sol_transfers(tx: dict, destination_wallet: str) -> list[dict]:
    """Extract parsed System Program transfer instructions into one wallet."""
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return []
    signatures = ((tx.get("transaction") or {}).get("signatures") or [])
    signature = signatures[0] if signatures else None
    found = []
    for index, ix in enumerate(_instructions(tx)):
        parsed = ix.get("parsed") or {}
        info = parsed.get("info") or {}
        if (ix.get("program") != "system" and ix.get("programId") != SYSTEM_PROGRAM) or parsed.get("type") != "transfer":
            continue
        source, destination = info.get("source"), info.get("destination")
        if destination != destination_wallet or not source or source == destination_wallet:
            continue
        try:
            lamports = int(info.get("lamports"))
        except (TypeError, ValueError):
            continue
        if lamports <= 0:
            continue
        found.append({"source_wallet": source, "destination_wallet": destination_wallet,
            "lamports": lamports, "sol": lamports / LAMPORTS_PER_SOL,
            "block_time": tx.get("blockTime"), "signature": signature,
            "instruction_index": index, "evidence": "parsed_system_transfer"})
    return found

def summarize_funding_wallet(wallet: str, first_buy_at: str, transfers: list[dict]) -> dict:
    return {"wallet": wallet, "first_observed_buy_at": first_buy_at,
        "direct_incoming_transfer_count": len(transfers),
        "direct_incoming_sol_total": sum(x["sol"] for x in transfers),
        "direct_sources": sorted({x["source_wallet"] for x in transfers}),
        "transfers": transfers,
        "interpretation": "Observed direct SOL transfer sources only; no identity or coordination inference."}

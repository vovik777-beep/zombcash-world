"""Build a conservative, public-safe daily Launch Radar report from SOI observations."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Iterable

from soi.analytics.timeline import build_first_hour_timeline
from soi.models import Observation
from soi.storage import ObservationStore


def _dt(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _event(item: Observation) -> dict:
    payload = item.payload if isinstance(item.payload, dict) else {}
    value = payload.get("event")
    return value if isinstance(value, dict) else {}


def _socials(rows: list[Observation]) -> dict:
    """Only claim no links when a source explicitly returned a links field."""
    for item in reversed(rows):
        if item.kind != "profile_observed":
            continue
        event = _event(item)
        if "links" not in event and "socials" not in event:
            continue
        raw = event.get("links") if "links" in event else event.get("socials")
        links = raw if isinstance(raw, list) else (
            [{"type": key, "url": value} for key, value in raw.items()]
            if isinstance(raw, dict) else []
        )
        result = {"observed": True, "website": None, "twitter": None,
                  "telegram": None, "discord": None, "other": []}
        for link in links:
            if not isinstance(link, dict) or not link.get("url"):
                continue
            kind = str(link.get("type") or link.get("label") or "").lower()
            url = str(link["url"])
            if "web" in kind or "site" in kind:
                result["website"] = url
            elif "twitter" in kind or "x" == kind:
                result["twitter"] = url
            elif "telegram" in kind:
                result["telegram"] = url
            elif "discord" in kind:
                result["discord"] = url
            else:
                result["other"].append(url)
        return result
    return {"observed": False, "website": None, "twitter": None,
            "telegram": None, "discord": None, "other": []}


def build_daily_report(observations: Iterable[Observation], report_date: str) -> dict:
    """Create the exact schema consumed by the Russian website Launch Radar.

    Unknown socials, promotion, first-hour data and valuation remain unknown;
    the exporter never turns missing observations into negative findings.
    """
    target = date.fromisoformat(report_date)
    items = list(observations)
    grouped: dict[str, list[Observation]] = {}
    for item in items:
        if item.kind != "token_created" or not item.mint or not item.event_at:
            continue
        launch_dt = _dt(item.event_at)
        if launch_dt and launch_dt.date() == target:
            grouped.setdefault(item.mint, []).append(item)

    launches = []
    errors = []
    for mint, mint_rows in sorted(grouped.items(), key=lambda pair: min(_dt(x.event_at) for x in pair[1])):
        launch_rows = sorted(mint_rows, key=lambda x: (_dt(x.event_at), x.source_ref))
        launch = launch_rows[0]
        event = _event(launch)
        mint_observations = [x for x in items if x.mint == mint]
        promo_rows = [x for x in mint_observations if x.kind in (
            "paid_ad_observed", "boost_observed", "paid_order_observed")]
        promotion = {
            "observed": bool(promo_rows),
            "boosted": any(x.kind == "boost_observed" for x in promo_rows),
            "paid_order": any(x.kind in ("paid_ad_observed", "paid_order_observed") for x in promo_rows),
            "evidence_count": len(promo_rows),
            "evidence_types": sorted({x.kind for x in promo_rows}),
        }
        snapshots = []
        for item in mint_observations:
            if item.kind != "market_snapshot" or not item.observed_at:
                continue
            snap = _event(item)
            observed = _dt(item.observed_at)
            if observed:
                snapshots.append((observed, snap))
        snapshots.sort(key=lambda x: x[0])
        latest = snapshots[-1][1] if snapshots else {}
        first_hour_caps = []
        launch_at = _dt(launch.event_at)
        first_hour_snapshots = []
        for observed, snap in snapshots:
            if launch_at <= observed <= launch_at + timedelta(minutes=60):
                first_hour_snapshots.append((observed, snap))
                cap = snap.get("market_cap_usd")
                if isinstance(cap, (int, float)) and not isinstance(cap, bool):
                    first_hour_caps.append(float(cap))
        expected_offsets = (1, 5, 15, 30, 60)
        observed_offsets = sorted({
            int(snap["scheduled_offset_minute"])
            for _, snap in first_hour_snapshots
            if str(snap.get("scheduled_offset_minute", "")).isdigit()
            and int(snap["scheduled_offset_minute"]) in expected_offsets
        })
        try:
            timeline = build_first_hour_timeline(items, mint)
            events = timeline.get("events", [])
            first_hour = {
                "status": "partial" if events else "unknown",
                "trade_count": timeline.get("event_count") if events else None,
                "buy_count": sum(1 for x in events if x.get("side") == "buy") if events else None,
                "sell_count": sum(1 for x in events if x.get("side") == "sell") if events else None,
                "unique_buyers": timeline.get("unique_buyers") if events else None,
                "unique_sellers": timeline.get("unique_sellers") if events else None,
                "coverage_note": timeline.get("coverage_note"),
                "market_snapshot_count": len(first_hour_snapshots),
                "scheduled_snapshot_offsets": observed_offsets,
                "expected_snapshot_offsets": list(expected_offsets),
                "snapshot_coverage_ratio": len(observed_offsets) / len(expected_offsets),
                "peak_market_cap_usd": max(first_hour_caps) if first_hour_caps else None,
            }
        except Exception as exc:
            first_hour = {"status": "unknown", "trade_count": None, "buy_count": None,
                          "sell_count": None, "unique_buyers": None, "unique_sellers": None,
                          "coverage_note": "Временная шкала неоднозначна: " + str(exc)[:180]}
            errors.append({"mint": mint, "stage": "first_hour", "error": str(exc)[:240]})
        social = _socials(mint_observations)
        launches.append({
            "mint": mint,
            "name": event.get("name"),
            "symbol": event.get("symbol"),
            "venue": "Pump.fun",
            "launch_at": launch_at.isoformat(),
            "first_seen_at": launch.observed_at,
            "market_cap_usd": latest.get("market_cap_usd"),
            "liquidity_usd": latest.get("liquidity_usd"),
            "market_snapshot_observed_at": snapshots[-1][0].isoformat() if snapshots else None,
            "market_snapshot_count": len(snapshots),
            "socials": social,
            "promotion": promotion,
            "promoted": promotion["observed"],
            "market_cap_milestone_1m": {
                "reached": any(value >= 1_000_000 for value in first_hour_caps),
                "evidence": "observed_market_cap_usd_within_first_hour" if any(value >= 1_000_000 for value in first_hour_caps) else "not_observed_or_unknown",
            },
            "first_hour": first_hour,
            "sources": sorted({x.source for x in mint_observations}),
        })

    known_social = [x for x in launches if x["socials"]["observed"]]
    summary = {
        "launches_detected": len(launches),
        "without_social_links": sum(
            not any(x["socials"].get(k) for k in ("website", "twitter", "telegram", "discord", "other"))
            for x in known_social
        ),
        "social_link_status_observed": len(known_social),
        "promoted_launches": sum(x["promotion"]["observed"] for x in launches),
        "reached_market_cap_1m": sum(x["market_cap_milestone_1m"]["reached"] for x in launches),
    }
    # Report all collectors that produced observations, even when the bounded scan found no token_created event.
    source_names = sorted({
        item.source for item in items
        if item.source and _dt(item.observed_at) and _dt(item.observed_at).date() == target
    })
    return {
        "schema_version": "1.0",
        "report_date": target.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "partial" if errors or not launches else "ok",
        "summary": summary,
        "coverage": {
            "launch_source": "Pump.fun program event scanner",
            "sources_observed": source_names,
            "launches_in_report": len(launches),
            "social_link_checks": len(known_social),
            "note": "Охват ограничен наблюдениями SOI и глубиной доступной истории; отсутствие записи не доказывает отсутствие события.",
        },
        "methodology": "Показываются только подтверждённые наблюдения. Отсутствующие соцссылки, продвижение, снимки и сделки остаются неизвестными, если источник не проверил соответствующее поле. Market Cap не заменяется FDV.",
        "launches": launches,
        "errors": errors,
    }



def build_live_report(observations: Iterable[Observation], now: datetime | None = None,
                      history_days: int = 365) -> dict:
    """Build a rolling 24-hour view plus a persistent UTC daily activity series."""
    if not 1 <= history_days <= 365:
        raise ValueError("history_days must be between 1 and 365")
    current = now or datetime.now(timezone.utc)
    current = current.replace(tzinfo=timezone.utc) if current.tzinfo is None else current.astimezone(timezone.utc)
    window_start = current - timedelta(hours=24)
    items = list(observations)

    launch_rows = []
    for report_day in sorted({window_start.date(), current.date()}):
        daily = build_daily_report(items, report_day.isoformat())
        for launch in daily.get("launches", []):
            launched_at = _dt(launch.get("launch_at"))
            if launched_at and window_start <= launched_at <= current:
                launch_rows.append(launch)
    launch_rows.sort(key=lambda row: row.get("launch_at") or "", reverse=True)

    rolling = []
    for item in items:
        event_time = _dt(item.event_at) or _dt(item.observed_at)
        if event_time and window_start <= event_time <= current:
            rolling.append(item)
    kinds, sources, mints = {}, set(), set()
    for item in rolling:
        kinds[item.kind] = kinds.get(item.kind, 0) + 1
        if item.source:
            sources.add(item.source)
        if item.mint:
            mints.add(item.mint)

    history_start = current.date() - timedelta(days=history_days - 1)
    daily_buckets = {}
    for item in items:
        event_time = _dt(item.event_at) or _dt(item.observed_at)
        if not event_time or event_time.date() < history_start or event_time.date() > current.date():
            continue
        key = event_time.date().isoformat()
        bucket = daily_buckets.setdefault(key, {
            "launches_detected": 0, "pools_created": 0, "buys": 0, "sells": 0,
            "observations": 0, "unique_mints": set(), "sources": set(),
        })
        bucket["observations"] += 1
        if item.mint:
            bucket["unique_mints"].add(item.mint)
        if item.source:
            bucket["sources"].add(item.source)
        if item.kind == "token_created":
            bucket["launches_detected"] += 1
        elif item.kind == "pool_created":
            bucket["pools_created"] += 1
        elif item.kind == "trade_buy":
            bucket["buys"] += 1
        elif item.kind == "trade_sell":
            bucket["sells"] += 1

    # Include only dates with observed source data. An absent day is unknown,
    # not proof that no launches or trades occurred.
    daily_history = []
    for key, bucket in sorted(daily_buckets.items()):
        daily_history.append({
            "date": key,
            "launches_detected": bucket["launches_detected"],
            "pools_created": bucket["pools_created"],
            "buys": bucket["buys"],
            "sells": bucket["sells"],
            "observations": bucket["observations"],
            "unique_mints": len(bucket["unique_mints"]),
            "sources_observed": sorted(bucket["sources"]),
        })

    report = build_daily_report(items, current.date().isoformat())
    report["rolling_24h"] = {
        "window_start": window_start.isoformat(),
        "window_end": current.isoformat(),
        "summary": {
            "launches_detected": len(launch_rows),
            "pools_created": kinds.get("pool_created", 0),
            "buys": kinds.get("trade_buy", 0),
            "sells": kinds.get("trade_sell", 0),
            "observations": len(rolling),
            "unique_mints": len(mints),
            "sources_observed": sorted(sources),
        },
        "launches": launch_rows,
    }
    report["daily_history"] = daily_history
    report["history"] = {
        "period_days": history_days,
        "start_date": history_start.isoformat(),
        "end_date": current.date().isoformat(),
        "timezone": "UTC",
        "note": "Counts include only events stored by the SOI collectors; historical coverage depends on scanner backfill and RPC availability.",
    }
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Export a website-ready SOI daily Launch Radar JSON report")
    parser.add_argument("--db", required=True, help="SOI SQLite database")
    parser.add_argument("--date", required=True, help="UTC report date YYYY-MM-DD")
    parser.add_argument("--out", required=True, help="Output JSON path")
    args = parser.parse_args(argv)
    report = build_daily_report(ObservationStore(Path(args.db)).observations(), args.date)
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Exported {report['summary']['launches_detected']} launches to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

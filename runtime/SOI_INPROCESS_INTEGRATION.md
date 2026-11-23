# SOI status in the full public runtime

SOI (Solana Outbreak Intelligence) source modules and their unit tests are preserved under `soi/` and `tests/soi/`.

**Current runtime status:** SOI is explicitly disabled in `bot.py` pending a full redesign. The Telegram bot does not register SOI collection or first-hour snapshot jobs. No separate SOI process is started.

The public migration deliberately preserves any existing `/data/soi/` directory. Disabling SOI must not erase its historical database or reports. The runtime startup logs the existing directory but does not delete, reset, rename, or rewrite it.

The SOI API/report code remains present for compatibility and future work, but its output may be unavailable while collection is disabled. Do not infer that live collection is running from the presence of the source modules or the historical report endpoint.

Boost Monitor continues to run independently on its existing cadence. It is not wired into SOI observation ingestion in this runtime snapshot; do not add a second boost poller.

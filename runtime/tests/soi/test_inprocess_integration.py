from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_soi_is_scheduled_inside_the_existing_telegram_bot():
    bot = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert "from soi.runtime import run_cycle as run_soi_cycle" in bot
    assert "application.job_queue.run_repeating(" in bot
    assert "soi_collection_job," in bot
    assert "SOI_ENABLED" in bot


def test_start_script_does_not_create_a_separate_soi_process():
    script = (ROOT / "start.sh").read_text(encoding="utf-8")
    assert "soi_guard.py" not in script
    assert "SOI_PID" not in script
    assert "python -u bot.py &" in script

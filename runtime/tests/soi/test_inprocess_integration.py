from pathlib import Path


RUNTIME = Path(__file__).resolve().parents[2]
REPO_ROOT = RUNTIME.parent


def test_soi_is_explicitly_disabled_without_erasing_historical_data():
    bot = (RUNTIME / "bot.py").read_text(encoding="utf-8")
    assert "SOI_ENABLED = False" in bot
    assert "preserve_disabled_soi_data()" in bot
    assert "shutil.rmtree(data_dir)" not in bot
    assert "from soi.runtime import run_cycle as run_soi_cycle" not in bot


def test_root_launcher_keeps_soi_out_of_separate_processes():
    script = (REPO_ROOT / "start.sh").read_text(encoding="utf-8")
    assert "soi_guard.py" not in script
    assert "SOI_PID" not in script
    assert 'runtime/bot.py' in script
    assert 'runtime/news_worker.py' in script

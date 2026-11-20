"""Code guards that don't need the model: the Python guard, speed words, leaked tool calls, 'say that again',
chat recall. Ported from the round-11 night-test checks."""
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import agent
import tools


# ---------------------------------------------------------------- Python guard (audit hook in a child process)
def test_guard_blocks_write_outside_temp(tmp_path_factory):
    target = Path.home() / "jarvis_guard_test_should_not_exist.txt"
    out = tools.run_python(f"open(r'{target}', 'w').write('x')", guarded=True)
    assert out.startswith("GUARD_BLOCKED") and not target.exists()


def test_guard_blocks_from_import_trick():
    # 'from os import remove' slipped past the old regex-only check
    victim = Path.home() / "jarvis_guard_victim.txt"
    victim.write_text("keep")
    try:
        out = tools.run_python(f"from os import remove; remove(r'{victim}')", guarded=True)
        assert out.startswith("GUARD_BLOCKED") and victim.exists()
    finally:
        victim.unlink(missing_ok=True)


def test_guard_allows_changes_inside_temp():
    f = Path(tempfile.gettempdir()) / "jarvis_guard_tmp.txt"
    f.write_text("x")
    tools.run_python(f"from os import remove; remove(r'{f}'); print('gone')", guarded=True)
    assert not f.exists()


@pytest.mark.parametrize("code", ["import subprocess; subprocess.run('whoami')",
                                  "import socket; socket.create_connection(('example.com', 80))",
                                  "import ctypes; ctypes.windll.user32.MessageBeep(0)"])
def test_guard_blocks_programs_network_native(code):
    assert tools.run_python(code, guarded=True).startswith("GUARD_BLOCKED")


def test_guard_lets_plain_code_run():
    assert "45" in tools.run_python("print(sum(range(10)))", guarded=True)


# ---------------------------------------------------------------- speaking speed from the user's own words
@pytest.mark.parametrize("words,want", [("two times", "2"), ("one time", "1"), ("slowest", "0.6"),
                                        ("zero point five x", "0.5"), ("one point five X", "1.5"),
                                        ("normal", "1.0"), ("a bit faster", None)])
def test_speed_from_words(words, want):
    assert tools.speed_from_words(words) == want


# ---------------------------------------------------------------- honesty helpers
def test_tool_call_written_as_text_becomes_a_real_call():
    calls, rest = agent.leaked_calls('<|tool_call>call:jarvis_settings{setting:<|"|>speaking speed<|"|>,'
                                     'value:<|"|>slower<|"|>}<tool_call|>')
    assert len(calls) == 1 and calls[0]["function"]["name"] == "jarvis_settings" and rest.strip() == ""


@pytest.mark.parametrize("text", ["say that again", "repeat", "read the story again", "repeat the IP"])
def test_repeat_requests(text):
    assert agent.REPEAT_ONLY.search(text)


@pytest.mark.parametrize("text", ["tell me the time", "read the news", "say hello"])
def test_not_repeat_requests(text):
    assert not agent.REPEAT_ONLY.search(text)


# ---------------------------------------------------------------- chat recall (temp file, not your history)
def test_recall_chats_by_day(tmp_path, monkeypatch):
    y, older = datetime.now() - timedelta(days=1), datetime.now() - timedelta(days=3)
    rows = [{"time": y.replace(hour=10).isoformat(timespec="seconds"), "user": "what's the weather", "answer": "It is 24 degrees and sunny."},
            {"time": y.replace(hour=11).isoformat(timespec="seconds"), "user": "open notepad", "answer": "Opened Notepad."},
            {"time": older.isoformat(timespec="seconds"), "user": "weather tomorrow", "answer": "Rain expected."}]
    chats = tmp_path / "chat_history.jsonl"
    chats.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(tools, "CHATS", chats)
    out = tools.recall_chats(query="weather", when="yesterday")
    assert "24" in out and "Rain" not in out

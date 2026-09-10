"""Базовые тесты языко-агностичных инструментов (Этап 7 roadmap: pytest)."""

import pytest
import importlib

from tools import filesystem as fs


@pytest.fixture
def sample_project(tmp_path):
    (tmp_path / "player.gd").write_text(
        "extends Node\n\nfunc take_damage(amount):\n    health -= amount\n",
        encoding="utf-8",
    )
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "utils.js").write_text(
        "function takeDamage(amount) {\n  health -= amount;\n}\n",
        encoding="utf-8",
    )
    return tmp_path


def test_list_tree_shows_all_files(sample_project):
    tree = fs.list_tree(str(sample_project))
    assert "player.gd" in tree["content"]
    assert "utils.js" in tree["content"]
    assert "sub" in tree["content"]


def test_search_content_is_language_agnostic(sample_project):
    # Один и тот же поиск находит совпадения и в .gd, и в .js —
    # инструмент не знает и не должен знать про синтаксис языка.
    result = fs.search_content(str(sample_project), "takeDamage")
    files_hit = {m["file"] for m in result["matches"]}
    assert any("utils.js" in f for f in files_hit)

    result_case_insensitive = fs.search_content(str(sample_project), "take_damage")
    files_hit2 = {m["file"] for m in result_case_insensitive["matches"]}
    assert any("player.gd" in f for f in files_hit2)


def test_read_file_reports_truncation(sample_project):
    result = fs.read_file(str(sample_project), "player.gd", max_chars=5)
    assert result["truncated"] is True
    assert len(result["content"]) == 5



def test_read_file_continuation_with_next_offset(sample_project):
    first = fs.read_file(str(sample_project), "player.gd", max_chars=5)
    second = fs.read_file(
        str(sample_project), "player.gd", max_chars=5, offset=first["next_offset"]
    )
    full_content = (sample_project / "player.gd").read_text()

    assert first["content"] + second["content"] == full_content[:10]
    assert second["offset"] == first["next_offset"]    


def test_read_file_rejects_path_outside_project(sample_project):
    with pytest.raises(ValueError):
        fs.read_file(str(sample_project), "../../etc/passwd")


def test_write_file_rejects_path_outside_project(sample_project):
    with pytest.raises(ValueError):
        fs.write_file(
            str(sample_project), "../../etc/passwd", "hacked", write_enabled=True
        )        


def test_write_file_respects_write_enabled(sample_project):
    # Архитектурное решение (см. docstring write_file): контроль записи —
    # это общий тумблер write_enabled на всю сессию, а не список
    # конкретных разрешённых файлов.
    with pytest.raises(PermissionError):
        fs.write_file(str(sample_project), "player.gd", "new content")

    result = fs.write_file(
        str(sample_project), "player.gd", "new content", write_enabled=True
    )
    assert result["created"] is False
    assert (sample_project / "player.gd").read_text() == "new content"


def test_write_file_creates_new_file_when_enabled(sample_project):
    result = fs.write_file(
        str(sample_project), "new_script.gd", "extends Node\n", write_enabled=True
    )
    assert result["created"] is True
    assert (sample_project / "new_script.gd").exists()





def test_estimate_tokens_grows_with_length():
    short = fs.estimate_tokens("hello")
    longer = fs.estimate_tokens("hello " * 100)
    assert longer > short
    

def test_get_current_datetime_returns_expected_fields():
    result = fs.get_current_datetime()
    assert "datetime" in result
    assert "date" in result
    assert "time" in result
    assert "weekday" in result
    assert result["timezone"] == "локальное время компьютера"


def test_get_current_datetime_with_valid_timezone():
    result = fs.get_current_datetime(timezone="UTC")
    assert result["timezone"] == "UTC"
    assert "error" not in result


def test_get_current_datetime_with_invalid_timezone_returns_error():
    result = fs.get_current_datetime(timezone="Not/A_Real_Zone")
    assert "error" in result

    

def test_search_content_respects_custom_ignore_dirs(tmp_path):
    (tmp_path / "keep_me").mkdir()
    (tmp_path / "keep_me" / "code.py").write_text("MAGIC_WORD = 1\n", encoding="utf-8")
    (tmp_path / "skip_me").mkdir()
    (tmp_path / "skip_me" / "code.py").write_text("MAGIC_WORD = 2\n", encoding="utf-8")

    result = fs.search_content(str(tmp_path), "MAGIC_WORD", ignore_dirs={"skip_me"})

    files_hit = {m["file"] for m in result["matches"]}
    assert any("keep_me" in f for f in files_hit)
    assert not any("skip_me" in f for f in files_hit)

    import importlib
import os as _os


def test_extra_ignore_dirs_from_env(monkeypatch):
    monkeypatch.setenv("EXTRA_IGNORE_DIRS", "my_custom_dir, another_dir")

    importlib.reload(fs)

    try:
        assert "my_custom_dir" in fs.DEFAULT_IGNORE_DIRS
        assert "another_dir" in fs.DEFAULT_IGNORE_DIRS
        assert ".git" in fs.DEFAULT_IGNORE_DIRS
    finally:
        monkeypatch.delenv("EXTRA_IGNORE_DIRS", raising=False)
        importlib.reload(fs)

    def test_list_tree_truncates_by_max_files(tmp_path):
        for i in range(5):
            (tmp_path / f"file_{i}.txt").write_text("x", encoding="utf-8")

        result = fs.list_tree(str(tmp_path), max_files=2)

        assert result["truncated"] is True
        assert result["reason"] == "file_limit"


def test_list_tree_truncates_by_max_seconds(tmp_path):
    (tmp_path / "file_0.txt").write_text("x", encoding="utf-8")
    (tmp_path / "file_1.txt").write_text("x", encoding="utf-8")

    result = fs.list_tree(str(tmp_path), max_seconds=0)

    assert result["truncated"] is True
    assert result["reason"] == "time_limit"


def test_search_content_truncates_by_max_files(tmp_path):
    for i in range(5):
        (tmp_path / f"file_{i}.py").write_text("MAGIC_WORD\n", encoding="utf-8")

    result = fs.search_content(str(tmp_path), "MAGIC_WORD", max_files=2)

    assert result["truncated"] is True
    assert result["reason"] == "file_limit"


def test_search_content_truncates_by_max_seconds(tmp_path):
    (tmp_path / "file_0.py").write_text("MAGIC_WORD\n", encoding="utf-8")

    result = fs.search_content(str(tmp_path), "MAGIC_WORD", max_seconds=0)

    assert result["truncated"] is True
    assert result["reason"] == "time_limit"    
"""Базовые тесты языко-агностичных инструментов (Этап 7 roadmap: pytest)."""

import pytest

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
    assert "player.gd" in tree
    assert "utils.js" in tree
    assert "sub" in tree


def test_search_content_is_language_agnostic(sample_project):
    # Один и тот же поиск находит совпадения и в .gd, и в .js —
    # инструмент не знает и не должен знать про синтаксис языка.
    matches = fs.search_content(str(sample_project), "takeDamage")
    files_hit = {m["file"] for m in matches}
    assert any("utils.js" in f for f in files_hit)

    matches_case_insensitive = fs.search_content(str(sample_project), "take_damage")
    files_hit2 = {m["file"] for m in matches_case_insensitive}
    assert any("player.gd" in f for f in files_hit2)


def test_read_file_reports_truncation(sample_project):
    result = fs.read_file(str(sample_project), "player.gd", max_chars=5)
    assert result["truncated"] is True
    assert len(result["content"]) == 5


def test_read_file_rejects_path_outside_project(sample_project):
    with pytest.raises(ValueError):
        fs.read_file(str(sample_project), "../../etc/passwd")


def test_write_file_respects_allowed_files(sample_project):
    with pytest.raises(PermissionError):
        fs.write_file(str(sample_project), "player.gd", "new content", allowed_files={"other.gd"})

    result = fs.write_file(str(sample_project), "player.gd", "new content", allowed_files={"player.gd"})
    assert result["created"] is False
    assert (sample_project / "player.gd").read_text() == "new content"


def test_write_file_creates_new_file_when_no_restriction(sample_project):
    result = fs.write_file(str(sample_project), "new_script.gd", "extends Node\n")
    assert result["created"] is True
    assert (sample_project / "new_script.gd").exists()


def test_estimate_tokens_grows_with_length():
    short = fs.estimate_tokens("hello")
    longer = fs.estimate_tokens("hello " * 100)
    assert longer > short

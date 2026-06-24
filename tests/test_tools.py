"""Tests for tool implementations."""
import re
import tempfile
from pathlib import Path

import pytest

from longform_agent.config import Config
from longform_agent.tools import PatchEditor, ShellRunner, _atomic_write


# ---------------------------------------------------------------------------
# PatchEditor
# ---------------------------------------------------------------------------

class TestPatchEditor:
    @pytest.fixture()
    def project_dir(self, tmp_path: Path) -> Path:
        (tmp_path / "chapters").mkdir()
        return tmp_path

    @pytest.fixture()
    def editor(self, project_dir: Path) -> PatchEditor:
        return PatchEditor(str(project_dir))

    def test_apply_new_file_patch(self, editor, project_dir):
        patch = (
            "--- /dev/null\n"
            "+++ b/chapters/01_intro.md\n"
            "@@ -0,0 +1,3 @@\n"
            "+# Introduction\n"
            "+\n"
            "+First paragraph.\n"
        )
        ok, path, err = editor.apply(patch)
        assert ok, err
        content = (project_dir / "chapters" / "01_intro.md").read_text()
        assert "First paragraph." in content

    def test_apply_edit_patch(self, editor, project_dir):
        ch = project_dir / "chapters" / "01_intro.md"
        ch.write_text("# Introduction\n\nOld text.\n", encoding="utf-8")
        patch = (
            "--- a/chapters/01_intro.md\n"
            "+++ b/chapters/01_intro.md\n"
            "@@ -1,3 +1,3 @@\n"
            " # Introduction\n"
            " \n"
            "-Old text.\n"
            "+New text.\n"
        )
        ok, path, err = editor.apply(patch)
        assert ok, err
        assert "New text." in ch.read_text()
        assert "Old text." not in ch.read_text()

    def test_apply_memory_patch(self, editor, project_dir):
        (project_dir / "agent_memory.md").write_text("# Memory\n", encoding="utf-8")
        patch = (
            "--- a/agent_memory.md\n"
            "+++ b/agent_memory.md\n"
            "@@ -1,1 +1,2 @@\n"
            " # Memory\n"
            "+- New fact.\n"
        )
        ok, path, err = editor.apply(patch)
        assert ok, err
        assert "New fact." in (project_dir / "agent_memory.md").read_text()

    def test_rejects_path_traversal(self, editor):
        patch = (
            "--- a/../../secret.txt\n"
            "+++ b/../../secret.txt\n"
            "@@ -0,0 +1 @@\n"
            "+bad\n"
        )
        ok, path, err = editor.apply(patch)
        assert not ok

    def test_rejects_disallowed_path(self, editor):
        patch = (
            "--- a/config.toml\n"
            "+++ b/config.toml\n"
            "@@ -0,0 +1 @@\n"
            "+bad\n"
        )
        ok, path, err = editor.apply(patch)
        assert not ok

    def test_empty_patch(self, editor):
        ok, path, err = editor.apply("")
        assert not ok

    def test_invalid_patch(self, editor):
        ok, path, err = editor.apply("this is not a diff")
        assert not ok


# ---------------------------------------------------------------------------
# _atomic_write
# ---------------------------------------------------------------------------

class TestAtomicWrite:
    def test_writes_content(self, tmp_path):
        target = tmp_path / "file.md"
        _atomic_write(target, "hello world")
        assert target.read_text() == "hello world"

    def test_no_tmp_file_left(self, tmp_path):
        target = tmp_path / "file.md"
        _atomic_write(target, "content")
        tmp = target.with_suffix(target.suffix + ".tmp")
        assert not tmp.exists()

    def test_overwrites_existing(self, tmp_path):
        target = tmp_path / "file.md"
        target.write_text("old")
        _atomic_write(target, "new")
        assert target.read_text() == "new"


# ---------------------------------------------------------------------------
# ShellRunner — basic smoke test (platform-safe)
# ---------------------------------------------------------------------------

class TestShellRunner:
    def test_run_echo(self, tmp_path):
        runner = ShellRunner(str(tmp_path))
        import sys
        # Use a python-based command so it works on all platforms
        code, output = runner.run(f'{sys.executable} -c "print(42)"')
        assert code == 0
        assert "42" in output

    def test_run_bad_command(self, tmp_path):
        runner = ShellRunner(str(tmp_path))
        code, output = runner.run("this_command_definitely_does_not_exist_xyz")
        assert code != 0

    def test_run_returns_no_output_marker(self, tmp_path):
        runner = ShellRunner(str(tmp_path))
        import sys
        code, output = runner.run(f'{sys.executable} -c "pass"')
        assert code == 0
        assert output == "(no output)"

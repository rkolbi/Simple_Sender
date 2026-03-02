from array import array
from pathlib import Path

import pytest

from simple_sender.gcode_parser import clean_gcode_line
from simple_sender.gcode_source import FileGcodeSource

pytestmark = pytest.mark.unit


def _offsets_for_clean_lines(path: Path) -> list[int]:
    offsets = []
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        while True:
            pos = handle.tell()
            raw = handle.readline()
            if not raw:
                break
            if clean_gcode_line(raw):
                offsets.append(pos)
    return offsets


def test_file_gcode_source_indexing_and_slicing(tmp_path: Path) -> None:
    path = tmp_path / "job.gcode"
    path.write_text(
        "\n".join(
            [
                "(comment)",
                "G0 X0 Y0",
                "  G1 X1 Y1 ; move",
                "",
                "G2 X2 Y2 I1 J0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    offsets = _offsets_for_clean_lines(path)
    source = FileGcodeSource(str(path), offsets)

    assert len(source) == 3
    assert source[0] == "G0 X0 Y0"
    assert source[1] == "G1 X1 Y1"
    assert source[-1] == "G2 X2 Y2 I1 J0"
    assert source[:2] == ["G0 X0 Y0", "G1 X1 Y1"]
    assert source[::2] == ["G0 X0 Y0", "G2 X2 Y2 I1 J0"]

    with pytest.raises(IndexError):
        _ = source[10]

    source.close()


def test_file_gcode_source_accepts_compact_offset_array(tmp_path: Path) -> None:
    path = tmp_path / "compact_offsets.gcode"
    path.write_text("G0 X0\nG1 X1\n", encoding="utf-8")
    offsets = array("Q", _offsets_for_clean_lines(path))
    source = FileGcodeSource(str(path), offsets)

    assert len(source) == 2
    assert source[1] == "G1 X1"

    source.close()


def test_file_gcode_source_skips_redundant_seek_for_sequential_reads(tmp_path: Path) -> None:
    path = tmp_path / "sequential_reads.gcode"
    path.write_text("G0 X0\nG1 X1\n", encoding="utf-8")
    source = FileGcodeSource(str(path), _offsets_for_clean_lines(path))

    class _TrackingFile:
        def __init__(self, wrapped) -> None:
            self._wrapped = wrapped
            self.seek_calls = 0

        @property
        def closed(self) -> bool:
            return bool(self._wrapped.closed)

        def tell(self) -> int:
            return int(self._wrapped.tell())

        def seek(self, offset: int) -> int:
            self.seek_calls += 1
            return int(self._wrapped.seek(offset))

        def readline(self) -> str:
            return str(self._wrapped.readline())

        def close(self) -> None:
            self._wrapped.close()

    wrapped = path.open("r", encoding="utf-8", errors="replace", newline="")
    tracker = _TrackingFile(wrapped)
    source._file = tracker  # type: ignore[assignment]

    try:
        assert source[0] == "G0 X0"
        assert source[1] == "G1 X1"
        assert tracker.seek_calls == 0
    finally:
        source.close()


def test_file_gcode_source_close_swallows_oserror(tmp_path: Path) -> None:
    path = tmp_path / "close_oserror.gcode"
    path.write_text("G0 X0\n", encoding="utf-8")
    source = FileGcodeSource(str(path), [0])

    class _BadCloseFile:
        closed = False

        def close(self) -> None:
            raise OSError("disk issue")

    source._file = _BadCloseFile()  # type: ignore[assignment]

    source.close()

    assert source._file is None


def test_file_gcode_source_close_raises_unexpected_error(tmp_path: Path) -> None:
    path = tmp_path / "close_runtime_error.gcode"
    path.write_text("G0 X0\n", encoding="utf-8")
    source = FileGcodeSource(str(path), [0])

    class _BadCloseFile:
        closed = False

        def close(self) -> None:
            raise RuntimeError("unexpected")

    source._file = _BadCloseFile()  # type: ignore[assignment]

    with pytest.raises(RuntimeError):
        source.close()


def test_file_gcode_source_already_clean_skips_reclean(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "already_clean.gcode"
    path.write_text("G0 X0\nG1 X1\n", encoding="utf-8")

    monkeypatch.setattr(
        "simple_sender.gcode_source.clean_gcode_line",
        lambda _line: (_ for _ in ()).throw(AssertionError("clean_gcode_line should not be called")),
    )

    source = FileGcodeSource(str(path), _offsets_for_clean_lines(path), already_clean=True)
    try:
        assert source[0] == "G0 X0"
        assert source[1] == "G1 X1"
    finally:
        source.close()

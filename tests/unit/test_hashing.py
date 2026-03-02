import hashlib

from simple_sender.utils.hashing import hash_lines


def test_hash_lines_returns_none_for_empty() -> None:
    assert hash_lines([]) is None
    assert hash_lines(None) is None


def test_hash_lines_is_deterministic() -> None:
    lines = ["G0 X0", "G1 X1 F100"]
    expected = hashlib.sha256()
    for line in lines:
        expected.update(line.encode("utf-8"))
        expected.update(b"\n")

    assert hash_lines(lines) == expected.hexdigest()
    assert hash_lines(lines) == hash_lines(list(lines))

import hashlib


def hash_lines(lines: list[str] | None) -> str | None:
    if not lines:
        return None
    hasher = hashlib.sha256()
    for ln in lines:
        hasher.update(ln.encode("utf-8"))
        hasher.update(b"\n")
    return hasher.hexdigest()

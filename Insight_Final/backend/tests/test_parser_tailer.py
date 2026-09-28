from backend.parser import parse_line
from backend.tailer import Tailer

def test_valid():
    p = parse_line("2026-01-01T00:00:00Z ERROR api DB down: x")
    assert p.is_error and p.service == "api" and p.message == "DB down: x"
    assert not parse_line("2026-01-01T00:00:00+00:00 warn a b").is_error
    assert parse_line("2026-01-01T00:00:00Z INFO svc").message == ""

def test_invalid():
    for l in ["", "  ", "garbage", "notatime ERROR a b", "2026-01-01T00:00:00Z BAD a b", "2026-01-01T00:00:00Z INFO"]:
        assert parse_line(l) is None

def test_tailer(tmp_path):
    f = tmp_path / "a.log"
    t = Tailer(str(f))
    assert t.poll() == []                      # missing file
    f.write_bytes(b"")
    assert t.poll() == []                      # empty
    with open(f, "ab") as h:
        h.write(b"one\ntw"); 
    assert t.poll() == ["one"]                 # partial buffered
    with open(f, "ab") as h:
        h.write(b"o\r\n\nthree\n")
    assert t.poll() == ["two", "three"]
    f.write_bytes(b"new\n")                    # truncation
    assert t.poll() == ["new"]

def test_skip_existing_and_from_start(tmp_path):
    f = tmp_path / "a.log"; f.write_text("old\n")
    assert Tailer(str(f)).poll() == []
    assert Tailer(str(f), from_start=True).poll() == ["old"]

from clipper.recorder import Anchor, Recorder, Segment


def _rec(tmp_path, n=10, buffer_minutes=0.1):
    """n contiguous 2 s segments ending at t=1000 + 2n, buffer of 6 s."""
    rec = Recorder("chan", tmp_path, buffer_minutes=buffer_minutes)
    anchor = Anchor(1000.0)
    folder = rec.root / "s1"
    folder.mkdir(parents=True)
    for i in range(n):
        p = folder / f"seg_{i:06d}.ts"
        p.write_bytes(b"x")
        rec.segments.append(Segment(p, i * 2.0, i * 2.0 + 2, anchor, "s1"))
    rec.stats.sessions.append("s1")
    return rec


def test_cleanup_deletes_old_segments(tmp_path):
    rec = _rec(tmp_path)                       # segments cover wall 1000..1020
    removed = rec.cleanup(now=1020)            # cutoff 1014: segs ending < 1014 go
    assert removed == 6
    assert [s.wall_start for s in rec.segments] == [1012, 1014, 1016, 1018]
    assert len(list((rec.root / "s1").glob("*.ts"))) == 4


def test_lock_protects_segments(tmp_path):
    rec = _rec(tmp_path)
    token = rec.lock(1003, 1005)               # overlaps segs [1002,1004] and [1004,1006]
    rec.cleanup(now=1020)
    assert [s.wall_start for s in rec.segments] == [1002, 1004, 1012, 1014, 1016, 1018]
    rec.unlock(token)
    rec.cleanup(now=1020)
    assert [s.wall_start for s in rec.segments] == [1012, 1014, 1016, 1018]


def test_anchor_refinement_moves_whole_session(tmp_path):
    rec = _rec(tmp_path, n=3)
    rec.segments[0].anchor.offset = 990.0
    assert [s.wall_start for s in rec.segments] == [990, 992, 994]


def test_segments_between(tmp_path):
    rec = _rec(tmp_path)
    got = rec.segments_between(1005, 1011)
    assert [s.wall_start for s in got] == [1004, 1006, 1008, 1010]


def test_dead_session_folder_removed(tmp_path):
    rec = _rec(tmp_path)
    old = rec.root / "s0"
    old.mkdir()
    (old / "index.csv").write_text("")
    rec.cleanup(now=1020)
    assert not old.exists()
    assert (rec.root / "s1").exists()

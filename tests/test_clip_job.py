import asyncio

from clipper.clip_job import ClipJob
from clipper.detector import Trigger
from clipper.recorder import Anchor, Recorder, Segment


def trig(start, end, final=True):
    return Trigger(channel="c", id=1, moment=start + 20, start=start, end=end, onset=start + 26,
                   detected_at=end, peak_score=30, baseline=10, ratio=3, top_messages=[],
                   reaction_counts={}, final=final)


def rec_with(tmp_path, ranges):
    """ranges: list of (session, wall_start, wall_end) covered with 2 s segments."""
    rec = Recorder("c", tmp_path)
    for session, a, b in ranges:
        anchor = Anchor(0.0)
        t = a
        while t < b:
            rec.segments.append(Segment(tmp_path / f"{session}_{t}.ts", t, t + 2, anchor, session))
            t += 2
    return rec


def make_job(rec, t):
    async def build():
        return ClipJob(t, rec)
    return asyncio.run(build())


def test_full_window_selected(tmp_path):
    rec = rec_with(tmp_path, [("s1", 0, 100)])
    job = make_job(rec, trig(40, 70))
    assert job.select()
    assert job.start == 40 and job.end == 70
    assert job.segments[0].wall_start == 40 and job.segments[-1].wall_end == 70


def test_small_gap_trims_to_best_run(tmp_path):
    # restart gap of 4 s at 64-68; best run is 40-64 (24 of 30 s = 80%)
    rec = rec_with(tmp_path, [("s1", 0, 64), ("s2", 68, 100)])
    job = make_job(rec, trig(40, 70))
    assert job.select()
    assert job.start == 40 and job.end == 64
    assert {s.session for s in job.segments} == {"s1"}


def test_too_much_missing_is_dropped(tmp_path):
    rec = rec_with(tmp_path, [("s1", 0, 50), ("s2", 60, 100)])   # best run 60-70 or 40-50
    job = make_job(rec, trig(40, 70))
    assert not job.select()
    assert "recorded" in job.dropped
    assert rec._lock_ranges == {}            # dropped jobs release their lock


def test_lock_follows_extension(tmp_path):
    rec = rec_with(tmp_path, [("s1", 0, 100)])
    job = make_job(rec, trig(40, 70, final=False))
    job.update(trig(40, 80))
    (lk,) = rec._lock_ranges.values()
    assert lk.end == 80
    job.release()
    assert rec._lock_ranges == {}

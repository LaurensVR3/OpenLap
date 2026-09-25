"""session_loader: the single load path (with secondary merge) and the UI's
session cache. Opening a session in the editor used to parse the file four
times, and again on every lap switch."""
import os
import threading
import time

import pytest

from session_loader import SessionCache, load_merged


def _counting_loader(result=None, delay=0.0):
    calls = []

    def load(path):
        calls.append(path)
        if delay:
            time.sleep(delay)
        return result if result is not None else object()
    return load, calls


def test_repeat_request_is_served_from_cache(tmp_path):
    f = tmp_path / 's.csv'
    f.write_text('x')
    load, calls = _counting_loader()
    cache = SessionCache()
    a = cache.get(str(f), loader=load)
    b = cache.get(str(f), loader=load)
    assert a is b and len(calls) == 1


def test_edited_file_is_reloaded(tmp_path):
    f = tmp_path / 's.csv'
    f.write_text('x')
    load, calls = _counting_loader()
    cache = SessionCache()
    cache.get(str(f), loader=load)
    f.write_text('longer contents')          # size (and mtime) change
    cache.get(str(f), loader=load)
    assert len(calls) == 2


def test_changed_secondary_offset_is_a_miss(tmp_path, monkeypatch):
    f, g = tmp_path / 'p.csv', tmp_path / 's.csv'
    f.write_text('x')
    g.write_text('y')
    monkeypatch.setattr('session_merge.merge_sessions', lambda p, s, o: ('merged', o))
    load, _ = _counting_loader()
    cache = SessionCache()
    assert cache.get(str(f), str(g), 1.0, loader=load) == ('merged', 1.0)
    assert cache.get(str(f), str(g), 2.5, loader=load) == ('merged', 2.5)


def test_concurrent_requests_parse_once(tmp_path):
    f = tmp_path / 's.csv'
    f.write_text('x')
    load, calls = _counting_loader(delay=0.2)
    cache = SessionCache()
    results = []
    threads = [threading.Thread(target=lambda: results.append(cache.get(str(f), loader=load)))
               for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1 and len({id(r) for r in results}) == 1


def test_load_error_reaches_every_waiter_and_is_not_cached(tmp_path):
    f = tmp_path / 's.csv'
    f.write_text('x')
    cache = SessionCache()

    def bad(path):
        raise ValueError('corrupt')
    with pytest.raises(ValueError):
        cache.get(str(f), loader=bad)
    load, calls = _counting_loader()
    cache.get(str(f), loader=load)            # a later good load is not blocked
    assert len(calls) == 1


def test_lru_evicts_oldest(tmp_path):
    load, calls = _counting_loader()
    cache = SessionCache(max_entries=2)
    paths = []
    for name in 'abc':
        p = tmp_path / f'{name}.csv'
        p.write_text(name)
        paths.append(str(p))
        cache.get(str(p), loader=load)
    cache.get(paths[0], loader=load)          # 'a' was evicted by 'c'
    assert len(calls) == 4


def test_load_merged_real_files_adds_qualified_secondary_channels(racebox_car_csv_path, tmp_path):
    import shutil
    second = tmp_path / 'logger.csv'
    shutil.copy(racebox_car_csv_path, second)
    sess = load_merged(racebox_car_csv_path, str(second), 0.0)
    assert 'Speed (logger.csv)' in sess.extra_channel_meta


def test_load_merged_without_secondary_file_is_just_the_primary(racebox_car_csv_path, tmp_path):
    sess = load_merged(racebox_car_csv_path, str(tmp_path / 'missing.csv'), 0.0)
    assert not any('(' in k for k in sess.extra_channel_meta)

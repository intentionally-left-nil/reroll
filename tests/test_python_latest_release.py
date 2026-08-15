"""Unit tests for `reroll.filename.python_latest_release`."""

from __future__ import annotations

import concurrent.futures
import json
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from reroll.errors import CacheWriteError, NetworkFetchError, UpstreamDataError
from reroll.filename.python_latest_release import (
    CACHE_FILENAME_PREFIX,
    DEFAULT_CACHE_DIR,
    DEFAULT_PYTHON_RELEASES_URL,
    _cached_files,
    _download,
    _ensure_fresh_cache,
    _is_stale,
    _latest_minor_from_releases,
    latest_python_minor,
    resolve_upper_bound,
)

# --------------------------------------------------------------------------
# `DEFAULT_CACHE_DIR` (docs/wheel_filename.md: "We are already using
# `$HOME/.cache/reroll` for parselmouth data, so we can add the file here")
# --------------------------------------------------------------------------


def test_default_cache_dir_is_home_cache_reroll() -> None:
    assert Path.home() / ".cache" / "reroll" == DEFAULT_CACHE_DIR


# --------------------------------------------------------------------------
# `_latest_minor_from_releases`
# --------------------------------------------------------------------------


def _releases_payload(names: list[str]) -> dict:
    """A minimal endoflife.date response shape carrying only the field
    `_latest_minor_from_releases` reads.
    """
    return {"result": {"releases": [{"name": name} for name in names]}}


class TestLatestMinorFromReleases:
    def test_picks_the_highest_3x_minor(self) -> None:
        data = _releases_payload(["3.14", "3.13", "3.12", "3.9"])
        assert _latest_minor_from_releases(data) == 14

    def test_ignores_non_3x_major_versions(self) -> None:
        data = _releases_payload(["3.14", "2.7", "2.6"])
        assert _latest_minor_from_releases(data) == 14

    def test_ignores_patch_level_names(self) -> None:
        """Only bare `3.<minor>` release names count -- endoflife.date's
        `releases[].name` is always minor-only, but `latest.name` (ignored
        here) carries the patch, e.g. `3.14.7`.
        """
        data = _releases_payload(["3.14", "3.14.7"])
        assert _latest_minor_from_releases(data) == 14

    def test_raises_when_no_3x_release_is_present(self) -> None:
        data = _releases_payload(["2.7"])
        with pytest.raises(UpstreamDataError, match="3.x"):
            _latest_minor_from_releases(data)


# --------------------------------------------------------------------------
# `resolve_upper_bound`
# --------------------------------------------------------------------------


class TestResolveUpperBound:
    def test_minor_only_string_parses_directly_without_a_lookup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _fail() -> int:
            raise AssertionError("must not look up a default when a bound is given")

        monkeypatch.setattr("reroll.filename.python_latest_release.latest_python_minor", _fail)

        assert resolve_upper_bound("3.15") == 15

    def test_none_defers_to_latest_python_minor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("reroll.filename.python_latest_release.latest_python_minor", lambda: 11)

        assert resolve_upper_bound(None) == 11

    @pytest.mark.parametrize("upper_bound", ["3.15.3", "15", "abc", "3.", "3"])
    def test_rejects_a_malformed_bound(self, upper_bound: str) -> None:
        with pytest.raises(ValueError, match="abi3_upper_bound"):
            resolve_upper_bound(upper_bound)


# --------------------------------------------------------------------------
# Cache file bookkeeping: `_cached_files` / `_is_stale`
# --------------------------------------------------------------------------


def _touch_cache_file(directory: Path, when: datetime) -> Path:
    path = directory / f"{CACHE_FILENAME_PREFIX}{int(when.timestamp())}.json"
    path.write_text("{}")
    return path


class TestCachedFiles:
    def test_empty_directory_returns_no_files(self, tmp_path: Path) -> None:
        assert _cached_files(tmp_path) == []

    def test_ignores_files_that_do_not_match_the_prefix(self, tmp_path: Path) -> None:
        (tmp_path / "unrelated.json").write_text("{}")
        assert _cached_files(tmp_path) == []

    def test_returns_newest_file_first(self, tmp_path: Path) -> None:
        now = datetime.now(UTC)
        older = _touch_cache_file(tmp_path, now - timedelta(days=5))
        newer = _touch_cache_file(tmp_path, now)

        assert _cached_files(tmp_path) == [newer, older]


class TestIsStale:
    def test_fresh_file_is_not_stale(self, tmp_path: Path) -> None:
        path = _touch_cache_file(tmp_path, datetime.now(UTC) - timedelta(hours=1))
        assert _is_stale(path) is False

    def test_file_older_than_one_day_is_stale(self, tmp_path: Path) -> None:
        path = _touch_cache_file(tmp_path, datetime.now(UTC) - timedelta(days=2))
        assert _is_stale(path) is True

    def test_file_just_under_one_day_old_is_not_yet_stale(self, tmp_path: Path) -> None:
        path = _touch_cache_file(tmp_path, datetime.now(UTC) - timedelta(hours=23, minutes=59))
        assert _is_stale(path) is False


# --------------------------------------------------------------------------
# `latest_python_minor` end to end, network mocked
# --------------------------------------------------------------------------


class _FakeHTTPResponse:
    def __init__(self, payload: bytes) -> None:
        self._remaining = payload

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        chunk = self._remaining if size < 0 else self._remaining[:size]
        self._remaining = self._remaining[len(chunk) :]
        return chunk


def _install_fake_urlopen(
    monkeypatch: pytest.MonkeyPatch, outcomes: list[bytes | urllib.error.HTTPError]
) -> list[urllib.request.Request]:
    """Monkeypatch `urllib.request.urlopen` to return/raise each of
    `outcomes` in order. Returns the requests it actually received.
    """
    remaining = list(outcomes)
    received: list[urllib.request.Request] = []

    def fake_urlopen(
        request: urllib.request.Request, timeout: float | None = None
    ) -> _FakeHTTPResponse:
        received.append(request)
        outcome = remaining.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return _FakeHTTPResponse(outcome)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return received


def _payload_bytes(names: list[str]) -> bytes:
    return json.dumps(_releases_payload(names)).encode()


class TestLatestPythonMinor:
    def test_downloads_and_caches_when_no_cache_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        requests = _install_fake_urlopen(monkeypatch, [_payload_bytes(["3.14", "3.13"])])

        minor = latest_python_minor(tmp_path)

        assert minor == 14
        assert len(requests) == 1
        assert _cached_files(tmp_path) != []

    def test_uses_fresh_cache_without_hitting_the_network(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = _touch_cache_file(tmp_path, datetime.now(UTC) - timedelta(hours=1))
        path.write_text(json.dumps(_releases_payload(["3.12"])))

        def _fail_urlopen(*args: object, **kwargs: object) -> None:
            raise AssertionError("must not hit the network for a fresh cache")

        monkeypatch.setattr("urllib.request.urlopen", _fail_urlopen)

        assert latest_python_minor(tmp_path) == 12

    def test_refreshes_a_stale_cache(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        stale = _touch_cache_file(tmp_path, datetime.now(UTC) - timedelta(days=2))
        stale.write_text(json.dumps(_releases_payload(["3.12"])))
        _install_fake_urlopen(monkeypatch, [_payload_bytes(["3.14"])])

        assert latest_python_minor(tmp_path) == 14

    def test_refreshing_deletes_older_cache_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        now = datetime.now(UTC)
        oldest = _touch_cache_file(tmp_path, now - timedelta(days=10))
        stale = _touch_cache_file(tmp_path, now - timedelta(days=2))
        stale.write_text(json.dumps(_releases_payload(["3.12"])))
        _install_fake_urlopen(monkeypatch, [_payload_bytes(["3.14"])])

        latest_python_minor(tmp_path)

        remaining = _cached_files(tmp_path)
        assert oldest not in remaining
        assert stale not in remaining
        assert len(remaining) == 1

    def test_second_scan_protects_a_concurrently_written_newer_file_from_deletion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """docs/wheel_filename.md: cleanup does "a second directory scan ...
        in case of race conditions" -- taken *after* the download, not off
        the pre-download listing. Simulate another process finishing its
        own refresh while ours is still in flight: our download's own
        `_download` call, as a side effect, drops in a file newer than the
        one we are about to write. The post-download scan must delete the
        *original* stale file but leave that concurrently-written newer
        file alone, even though it was invisible to the pre-download scan.
        """
        stale = _touch_cache_file(tmp_path, datetime.now(UTC) - timedelta(days=2))
        stale.write_text(json.dumps(_releases_payload(["3.12"])))

        real_download = _download
        concurrent_file = tmp_path / f"{CACHE_FILENAME_PREFIX}9999999999.json"

        def _download_and_simulate_a_racing_process(url: str, directory: Path) -> Path:
            concurrent_file.write_text(json.dumps(_releases_payload(["3.15"])))
            return real_download(url, directory)

        monkeypatch.setattr(
            "reroll.filename.python_latest_release._download",
            _download_and_simulate_a_racing_process,
        )
        _install_fake_urlopen(monkeypatch, [_payload_bytes(["3.14"])])

        _ensure_fresh_cache(tmp_path, DEFAULT_PYTHON_RELEASES_URL)

        remaining = _cached_files(tmp_path)
        assert stale not in remaining
        assert concurrent_file in remaining

    def test_creates_the_cache_directory_if_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        directory = tmp_path / "nested" / "reroll"
        _install_fake_urlopen(monkeypatch, [_payload_bytes(["3.14"])])

        latest_python_minor(directory)

        assert directory.is_dir()

    def test_sends_a_user_agent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        requests = _install_fake_urlopen(monkeypatch, [_payload_bytes(["3.14"])])

        latest_python_minor(tmp_path)

        assert requests[0].headers.get("User-agent")


# --------------------------------------------------------------------------
# `_download` atomicity -- concurrent readers/writers of the cache directory
# must never observe a partially-written file (a bare `write_bytes` straight
# to the final, glob-discoverable filename would let a concurrent
# `_cached_files` scan pick up a file that a still-in-progress download has
# only partially flushed).
# --------------------------------------------------------------------------


class _BlockingFakeHTTPResponse:
    """A fake `urlopen` response whose body is only handed over once the
    test explicitly releases `proceed`, with `entered` signaled first so the
    test can observe cache-directory state while the "download" is still in
    flight.
    """

    def __init__(self, payload: bytes, entered: threading.Event, proceed: threading.Event) -> None:
        self._payload = payload
        self._entered = entered
        self._proceed = proceed

    def __enter__(self) -> _BlockingFakeHTTPResponse:
        self._entered.set()
        self._proceed.wait(timeout=5)
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._payload


class TestDownloadIsAtomic:
    def test_no_cache_file_is_visible_until_the_download_completes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        entered = threading.Event()
        proceed = threading.Event()
        response = _BlockingFakeHTTPResponse(_payload_bytes(["3.14"]), entered, proceed)
        monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=None: response)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_download, DEFAULT_PYTHON_RELEASES_URL, tmp_path)
            entered.wait(timeout=5)

            # The download is still "in flight" (blocked on `proceed`) --
            # nothing glob-discoverable (i.e. visible to `_cached_files`)
            # should exist yet. A staged temp file may already be present
            # on disk at this point -- that's fine, it isn't findable by
            # the `python_version_*.json` pattern readers actually use.
            assert _cached_files(tmp_path) == []

            proceed.set()
            future.result(timeout=5)

        (path,) = _cached_files(tmp_path)
        assert _latest_minor_from_releases(json.loads(path.read_text())) == 14

    def test_a_failed_download_leaves_no_staged_file_behind(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _fail_urlopen(*args: object, **kwargs: object) -> None:
            raise urllib.error.URLError("boom")

        monkeypatch.setattr("urllib.request.urlopen", _fail_urlopen)

        with pytest.raises(NetworkFetchError):
            _download(DEFAULT_PYTHON_RELEASES_URL, tmp_path)

        assert list(tmp_path.iterdir()) == []

    def test_a_non_os_error_during_download_still_cleans_up_and_propagates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failure that isn't network/IO related (e.g. a bug elsewhere)
        must not be miscategorized as a `NetworkFetchError` -- it still
        propagates as itself, but the staged scratch file is still cleaned
        up either way.
        """

        def _fail_urlopen(*args: object, **kwargs: object) -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr("urllib.request.urlopen", _fail_urlopen)

        with pytest.raises(RuntimeError, match="boom"):
            _download(DEFAULT_PYTHON_RELEASES_URL, tmp_path)

        assert list(tmp_path.iterdir()) == []

    def test_a_failed_cache_install_raises_cache_write_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda request, timeout=None: _FakeHTTPResponse(_payload_bytes(["3.14"])),
        )
        monkeypatch.setattr(
            "pathlib.Path.replace",
            lambda self, target: (_ for _ in ()).throw(OSError("disk full")),
        )

        with pytest.raises(CacheWriteError):
            _download(DEFAULT_PYTHON_RELEASES_URL, tmp_path)

    def test_concurrent_downloads_never_produce_a_partially_written_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Many threads racing `_download` against the same cache directory
        -- often colliding on the same integer-second destination filename
        -- while other threads continuously read whatever is currently on
        disk. Every read must see either nothing or a complete, valid JSON
        payload; never a truncated/partial write.
        """
        payload = _payload_bytes(["3.14"])
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda request, timeout=None: _FakeHTTPResponse(payload),
        )

        stop = threading.Event()
        errors: list[BaseException] = []

        def read_loop() -> None:
            while not stop.is_set():
                for path in _cached_files(tmp_path):
                    try:
                        data = json.loads(path.read_text())
                    except (FileNotFoundError, json.JSONDecodeError) as exc:
                        errors.append(exc)
                        continue
                    if _latest_minor_from_releases(data) != 14:
                        errors.append(AssertionError(f"unexpected payload in {path}"))

        readers = [threading.Thread(target=read_loop) for _ in range(4)]
        for reader in readers:
            reader.start()

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [
                pool.submit(_download, DEFAULT_PYTHON_RELEASES_URL, tmp_path) for _ in range(64)
            ]
            concurrent.futures.wait(futures)

        stop.set()
        for reader in readers:
            reader.join(timeout=5)

        assert errors == []


# --------------------------------------------------------------------------
# Against the real published data (network)
# --------------------------------------------------------------------------


class TestLatestPythonMinorAgainstRealData:
    @pytest.mark.network
    def test_default_url_returns_a_plausible_minor(self, tmp_path: Path) -> None:
        minor = latest_python_minor(tmp_path, url=DEFAULT_PYTHON_RELEASES_URL)
        assert minor >= 13

"""Keep the default test suite separate from local accounts and browsers."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine


@event.listens_for(Engine, "connect")
def _fast_test_sqlite(dbapi_connection, _record):
    """Tests create dozens of throwaway SQLite files; durability is irrelevant.

    Without this every create_all() fsyncs per statement (42 tables), which on
    Windows disks costs 12-80 s per fresh database and made the suite time out.
    Applies to all engines, including tests that call create_engine() directly.
    """
    if type(dbapi_connection).__module__.split(".")[0] != "sqlite3":
        return
    cursor = dbapi_connection.cursor()
    try:
        # Only drop fsync. Changing journal_mode here would need an exclusive
        # lock and fails ("database is locked") when a test holds a second
        # connection open; app.db already selects WAL for its own engine.
        cursor.execute("PRAGMA synchronous=OFF")
    finally:
        cursor.close()


def pytest_configure(config):
    # main.py loads its configuration during import, before fixtures run.
    # Set the override here so collection never reads the user's config.yaml.
    root = tempfile.TemporaryDirectory(prefix="creatorhub-tests-")
    previous = os.environ.get("CREATORHUB_CONFIG_PATH")
    path = Path(root.name)
    config_file = path / "config.yaml"
    config_file.write_text(
        "server:\n  host: 127.0.0.1\n"
        f"storage:\n  db_path: {json.dumps(str(path / 'test.db'))}\n"
        "engine:\n"
        f"  profiles_dir: {json.dumps(str(path / 'profiles'))}\n"
        f"  media_dir: {json.dumps(str(path / 'media'))}\n",
        encoding="utf-8",
    )
    os.environ["CREATORHUB_CONFIG_PATH"] = str(config_file)

    def cleanup():
        if previous is None:
            os.environ.pop("CREATORHUB_CONFIG_PATH", None)
        else:
            os.environ["CREATORHUB_CONFIG_PATH"] = previous
        root.cleanup()

    config.add_cleanup(cleanup)


@pytest.fixture(autouse=True)
def no_real_browser(monkeypatch, request):
    """An unmocked browser boundary is a test failure, not a fallback launch.

    The existing, explicitly opted-in local CDP tests are the sole exception.
    Those tests use temporary profiles and a local HTTP fixture, not accounts.
    """
    if (request.node.get_closest_marker("local_cdp") is not None
            and os.environ.get("CREATORHUB_RUN_LOCAL_CDP") == "1"):
        return

    from patchright.async_api import BrowserType
    from patchright.async_api._context_manager import PlaywrightContextManager
    from patchright.sync_api import BrowserType as SyncBrowserType
    from patchright.sync_api._context_manager import (
        PlaywrightContextManager as SyncPlaywrightContextManager,
    )
    from app.browser.cdp import XhsCdpBackend

    def blocked(*_args, **_kwargs):
        # pytest.fail is intentionally not an Exception: production code's
        # catch-and-fallback paths must not hide a missing mock in a test.
        pytest.fail(
            "Real browser access is blocked in default tests. Mock the browser "
            "boundary, or explicitly run a local_cdp test with "
            "CREATORHUB_RUN_LOCAL_CDP=1.",
            pytrace=True,
        )

    monkeypatch.setattr(XhsCdpBackend, "_spawn_process", blocked)
    monkeypatch.setattr(PlaywrightContextManager, "start", blocked)
    monkeypatch.setattr(PlaywrightContextManager, "__aenter__", blocked)
    monkeypatch.setattr(SyncPlaywrightContextManager, "start", blocked)
    monkeypatch.setattr(SyncPlaywrightContextManager, "__enter__", blocked)
    for browser_type in (BrowserType, SyncBrowserType):
        for method in ("launch", "launch_persistent_context", "connect_over_cdp"):
            monkeypatch.setattr(browser_type, method, blocked)

"""Regression tests for first-run server-side path selection."""

from pathlib import Path

from src.core.server_path_browser import ServerPathBrowser


ROOT = Path(__file__).resolve().parents[1]


def test_server_path_browser_lists_directories_and_creates_child(tmp_path: Path) -> None:
    """Remote setup can browse/create folders on the server host."""
    (tmp_path / "Movies").mkdir()
    (tmp_path / "not-a-folder.txt").write_text("x")

    browser = ServerPathBrowser(seed_paths=[tmp_path])
    payload = browser.browse(str(tmp_path))

    assert payload["ok"] is True
    assert payload["path"] == str(tmp_path.resolve(strict=False))
    assert [entry["name"] for entry in payload["entries"]] == ["Movies"]
    assert any(root["path"] == str(tmp_path.resolve(strict=False)) for root in payload["roots"])

    created = browser.create_directory(str(tmp_path), "TV Shows")

    assert created["ok"] is True
    assert Path(created["path"]).name == "TV Shows"
    assert (tmp_path / "TV Shows").is_dir()


def test_server_path_browser_rejects_path_separator_folder_names(tmp_path: Path) -> None:
    """Folder creation must create one child only, never an arbitrary nested path."""
    browser = ServerPathBrowser()

    payload = browser.create_directory(str(tmp_path), "bad/name")

    assert payload["ok"] is False
    assert "separator" in payload["message"]
    assert not (tmp_path / "bad").exists()


def test_setup_template_is_category_driven_and_uses_server_browser() -> None:
    """The setup path panel loops over registered categories instead of hardcoding TV/movie only."""
    template = (ROOT / "src/web/templates/setup.html").read_text()
    setup_script = (ROOT / "src/web/static/js/pages/setup.js").read_text()
    browser_script = (ROOT / "src/web/static/js/components/serverPathBrowser.js").read_text()
    router = (ROOT / "src/web/routers/storage.py").read_text()

    assert "{% for category in categories %}" in template
    assert "{{ category.display_name }} Target Folder" in template
    assert "setup-category-path" in template
    assert "openServerPathBrowserForInput" in template
    assert "serverPathBrowser.js" in template
    assert "loadSetupRequirements" in setup_script
    assert "/api/storage/browse" in browser_script
    assert "/api/storage/mkdir" in browser_script
    assert "ServerPathBrowser" in router


def test_server_path_browser_groups_linux_external_media(monkeypatch, tmp_path: Path) -> None:
    """Linux media mount folders appear as first-class drive shortcuts."""
    media = tmp_path / "media"
    usb = media / "tommaso" / "BigDisk"
    usb.mkdir(parents=True)

    import src.core.server_path_browser as browser_module

    monkeypatch.setattr(browser_module.sys, "platform", "linux")
    monkeypatch.setattr(ServerPathBrowser, "_LINUX_MEDIA_ROOTS", (media,))
    monkeypatch.setattr(ServerPathBrowser, "_read_linux_mounts", lambda self: [])

    payload = ServerPathBrowser(seed_paths=[]).browse(str(tmp_path))
    groups = payload["root_groups"]
    drive_group = next(group for group in groups if group["label"] == "Drives and mounted media")

    assert any(entry["name"] == "BigDisk" for entry in drive_group["entries"])
    assert any(entry["path"] == str(usb.resolve(strict=False)) for entry in drive_group["entries"])


def test_setup_path_browser_renders_drive_sidebar() -> None:
    """The remote folder picker renders root groups in a left-column sidebar."""
    template = (ROOT / "src/web/templates/setup.html").read_text()
    script = (ROOT / "src/web/static/js/components/serverPathBrowser.js").read_text()
    browser = (ROOT / "src/core/server_path_browser.py").read_text()

    assert "serverPathBrowser.js" in template
    assert "path-browser-sidebar" in script
    assert "Server locations" in script
    assert "root_groups" in script
    assert "Drives and mounted media" in browser
    assert "_LINUX_MEDIA_ROOTS" in browser


def test_server_path_browser_infers_user_mounted_drive_from_configured_path(monkeypatch, tmp_path: Path) -> None:
    """Configured paths like ~/Mounted/Argh/Media/Series expose Argh as a drive."""
    mounted = tmp_path / "home" / "orb" / "Mounted"
    drive = mounted / "Argh"
    series = drive / "Media" / "Series"
    series.mkdir(parents=True)

    import src.core.server_path_browser as browser_module

    monkeypatch.setattr(browser_module.sys, "platform", "linux")
    monkeypatch.setattr(ServerPathBrowser, "_LINUX_MEDIA_ROOTS", ())
    monkeypatch.setattr(ServerPathBrowser, "_read_linux_mounts", lambda self: [])

    payload = ServerPathBrowser(seed_paths=[series]).browse(str(series))
    drive_group = next(group for group in payload["root_groups"] if group["label"] == "Drives and mounted media")

    assert any(entry["name"] == "Mounted" and entry["kind"] == "mounts" for entry in drive_group["entries"])
    assert any(entry["name"] == "Argh" and entry["path"] == str(drive.resolve(strict=False)) for entry in drive_group["entries"])

import plistlib

from lang_view.launchd import (
    DEFAULT_LABEL,
    default_agent_path,
    install_agent,
    render_plist,
    uninstall_agent,
    write_agent,
)


def test_render_plist_defaults():
    raw = render_plist()
    plist = plistlib.loads(raw)
    assert plist["Label"] == DEFAULT_LABEL
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True
    assert plist["ProgramArguments"][-2:] == ["lang_view", "watch"]


def test_render_plist_custom_args_and_paths():
    raw = render_plist(
        label="ai.test.x",
        program_args=["/usr/local/bin/lang-view", "watch", "--db", "/tmp/x.sqlite"],
        working_dir="/Users/me",
        stdout_path="/tmp/lv.out",
        stderr_path="/tmp/lv.err",
        env={"LV_KEY": "abc", "PATH": "/usr/bin"},
        run_at_load=False,
        keep_alive=False,
    )
    plist = plistlib.loads(raw)
    assert plist["Label"] == "ai.test.x"
    assert plist["ProgramArguments"][0] == "/usr/local/bin/lang-view"
    assert plist["WorkingDirectory"] == "/Users/me"
    assert plist["StandardOutPath"] == "/tmp/lv.out"
    assert plist["StandardErrorPath"] == "/tmp/lv.err"
    assert plist["EnvironmentVariables"]["LV_KEY"] == "abc"
    assert plist["RunAtLoad"] is False


def test_render_plist_omits_optional_keys_when_unset():
    raw = render_plist()
    plist = plistlib.loads(raw)
    assert "WorkingDirectory" not in plist
    assert "StandardOutPath" not in plist
    assert "EnvironmentVariables" not in plist


def test_default_agent_path_uses_label():
    p = default_agent_path("ai.example.thing")
    assert p.name == "ai.example.thing.plist"
    assert "LaunchAgents" in p.parts


def test_write_agent_creates_parents(tmp_path):
    target = tmp_path / "Library" / "LaunchAgents" / "ai.test.plist"
    raw = render_plist(label="ai.test")
    written = write_agent(target, raw)
    assert written.exists()
    assert plistlib.loads(written.read_bytes())["Label"] == "ai.test"


def test_install_agent_calls_launchctl_bootstrap(tmp_path):
    seen = {}

    def fake_runner(cmd, check):
        seen["cmd"] = cmd
        seen["check"] = check

    plist_path = tmp_path / "x.plist"
    plist_path.write_bytes(render_plist())
    install_agent(plist_path, runner=fake_runner)
    assert seen["cmd"][0:2] == ["launchctl", "bootstrap"]
    assert seen["cmd"][-1] == str(plist_path)
    assert seen["check"] is True


def test_uninstall_agent_calls_bootout_and_unlinks(tmp_path, monkeypatch):
    monkeypatch.setattr("lang_view.launchd.default_agent_path",
                        lambda label=DEFAULT_LABEL: tmp_path / f"{label}.plist")
    plist_path = tmp_path / "ai.test.plist"
    plist_path.write_bytes(render_plist(label="ai.test"))

    seen = {}

    def fake_runner(cmd, check):
        seen["cmd"] = cmd
        return None

    uninstall_agent(label="ai.test", runner=fake_runner)
    assert seen["cmd"][0:2] == ["launchctl", "bootout"]
    assert "/ai.test" in seen["cmd"][2]
    assert not plist_path.exists()


def test_uninstall_agent_is_idempotent_when_plist_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("lang_view.launchd.default_agent_path",
                        lambda label=DEFAULT_LABEL: tmp_path / f"{label}.plist")
    # Should not raise even though the file does not exist.
    uninstall_agent(label="ai.test", runner=lambda cmd, check: None)


def test_install_agent_cli_forwards_watch_flags(tmp_path, monkeypatch):
    """cmd_install_agent forwards --lang, --title-allow, --frames-dir, etc.

    The plist's ProgramArguments should contain the watch-loop config the
    user passed, so the agent runs with the same settings.
    """
    from lang_view.__main__ import main as cli_main

    monkeypatch.setattr("lang_view.launchd.is_launchctl_available", lambda: False)

    plist_path = tmp_path / "ai.test.plist"
    rc = cli_main([
        "install-agent",
        "--label", "ai.test",
        "--plist", str(plist_path),
        "--db", str(tmp_path / "x.db"),
        "--output", str(tmp_path / "x.jsonl"),
        "--lang", "ko",
        "--active-window",
        "--app-allow", "Chrome",
        "--title-allow", "Netflix,Disney+",
        "--min-confidence", "0.55",
        "--dedup-seconds", "5",
        "--group-lines",
        "--frames-dir", str(tmp_path / "frames"),
        "--snippets-dir", str(tmp_path / "snips"),
    ])
    assert rc == 0
    assert plist_path.exists()
    plist = plistlib.loads(plist_path.read_bytes())
    args = plist["ProgramArguments"]
    # Spot-check forwarded flags appear adjacent to their values.
    assert "--lang" in args
    assert args[args.index("--lang") + 1] == "ko"
    assert "--active-window" in args
    assert "--app-allow" in args
    assert args[args.index("--app-allow") + 1] == "Chrome"
    # --visible-window is its own action="store_true" flag.
    # Confirm it is forwarded when set:
    plist_path2 = tmp_path / "ai.test.vis.plist"
    cli_main([
        "install-agent",
        "--label", "ai.test.vis",
        "--plist", str(plist_path2),
        "--visible-window",
    ])
    plist2 = plistlib.loads(plist_path2.read_bytes())
    assert "--visible-window" in plist2["ProgramArguments"]

    # Per-window capture flags forward too.
    plist_path3 = tmp_path / "ai.test.cap.plist"
    cli_main([
        "install-agent",
        "--label", "ai.test.cap",
        "--plist", str(plist_path3),
        "--capture-window-app", "Chrome",
        "--capture-window-title", "Disney+",
    ])
    plist3 = plistlib.loads(plist_path3.read_bytes())
    args3 = plist3["ProgramArguments"]
    assert "--capture-window-app" in args3
    assert args3[args3.index("--capture-window-app") + 1] == "Chrome"
    assert "--capture-window-title" in args3
    assert args3[args3.index("--capture-window-title") + 1] == "Disney+"
    assert "--title-allow" in args
    assert args[args.index("--title-allow") + 1] == "Netflix,Disney+"
    assert "--min-confidence" in args
    assert args[args.index("--min-confidence") + 1] == "0.55"
    assert "--dedup-seconds" in args
    assert "--group-lines" in args
    assert "--frames-dir" in args
    assert args[args.index("--frames-dir") + 1] == str(tmp_path / "frames")
    assert "--snippets-dir" in args


def test_install_agent_with_bundle_uses_bundle_launcher(tmp_path, monkeypatch):
    """When --bundle is set, ProgramArguments[0] must point at the
    bundle's launcher script (not the raw python interpreter), so the
    agent inherits the bundle's TCC identity."""
    from lang_view.__main__ import main as cli_main
    from lang_view.bundle import write_app_bundle

    monkeypatch.setattr("lang_view.launchd.is_launchctl_available", lambda: False)

    bundle = tmp_path / "Lang-View.app"
    write_app_bundle(bundle, python_executable="/usr/bin/python3",
                     bundle_id="ai.lang-view")

    plist_path = tmp_path / "ai.test.bundled.plist"
    rc = cli_main([
        "install-agent",
        "--label", "ai.test.bundled",
        "--plist", str(plist_path),
        "--bundle", str(bundle),
        "--db", str(tmp_path / "x.db"),
    ])
    assert rc == 0
    plist = plistlib.loads(plist_path.read_bytes())
    args = plist["ProgramArguments"]
    expected_launcher = str(bundle / "Contents" / "MacOS" / "lang-view")
    assert args[0] == expected_launcher
    assert args[1] == "watch"  # subcommand for the bundle launcher
    # Plain python -m invocation should NOT be used.
    assert "python" not in args[0].split("/")[-1]
    # Forwarded flags still appear.
    assert "--db" in args


def test_install_subtitle_agent_forwards_flags(tmp_path, monkeypatch):
    """install-subtitle-agent must forward --url-match, --selector, --db,
    --pause-flag-file etc. into ProgramArguments so the loaded agent runs
    against the right tab and DB."""
    from lang_view.__main__ import main as cli_main

    monkeypatch.setattr("lang_view.launchd.is_launchctl_available", lambda: False)

    plist_path = tmp_path / "ai.test.subtitle.plist"
    rc = cli_main([
        "install-subtitle-agent",
        "--label", "ai.test.subtitle",
        "--plist", str(plist_path),
        "--db", str(tmp_path / "x.db"),
        "--output", str(tmp_path / "x.jsonl"),
        "--url-match", "disneyplus.com/play",
        "--selector", ".hive-subtitle-renderer-wrapper",
        "--interval", "0.5",
        "--lang-hint", "ja",
        "--pause-flag-file", str(tmp_path / "p.flag"),
    ])
    assert rc == 0
    plist = plistlib.loads(plist_path.read_bytes())
    args = plist["ProgramArguments"]
    assert "subtitle-watch" in args
    assert "watch" not in args  # NOT the OCR watch loop
    assert "--url-match" in args
    assert args[args.index("--url-match") + 1] == "disneyplus.com/play"
    assert "--selector" in args
    assert "--lang-hint" in args
    assert args[args.index("--lang-hint") + 1] == "ja"
    assert "--pause-flag-file" in args


def test_install_subtitle_agent_with_bundle(tmp_path, monkeypatch):
    from lang_view.__main__ import main as cli_main
    from lang_view.bundle import write_app_bundle

    monkeypatch.setattr("lang_view.launchd.is_launchctl_available", lambda: False)

    bundle = tmp_path / "Lang-View.app"
    write_app_bundle(bundle, python_executable="/usr/bin/python3")

    plist_path = tmp_path / "ai.test.subtitle.bundled.plist"
    rc = cli_main([
        "install-subtitle-agent",
        "--label", "ai.test.subtitle.bundled",
        "--plist", str(plist_path),
        "--bundle", str(bundle),
    ])
    assert rc == 0
    plist = plistlib.loads(plist_path.read_bytes())
    args = plist["ProgramArguments"]
    assert args[0].endswith("/Contents/MacOS/lang-view")
    assert args[1] == "subtitle-watch"


def test_install_menubar_agent_with_bundle_uses_bundle_launcher(tmp_path, monkeypatch):
    from lang_view.__main__ import main as cli_main
    from lang_view.bundle import write_app_bundle

    monkeypatch.setattr("lang_view.launchd.is_launchctl_available", lambda: False)

    bundle = tmp_path / "Lang-View.app"
    write_app_bundle(bundle, python_executable="/usr/bin/python3")

    plist_path = tmp_path / "ai.test.menubar-bundled.plist"
    rc = cli_main([
        "install-menubar-agent",
        "--label", "ai.test.menubar-bundled",
        "--plist", str(plist_path),
        "--bundle", str(bundle),
    ])
    assert rc == 0
    plist = plistlib.loads(plist_path.read_bytes())
    args = plist["ProgramArguments"]
    assert args[0].endswith("/Contents/MacOS/lang-view")
    assert args[1] == "menubar"


def test_install_menubar_agent_cli_writes_menubar_plist(tmp_path, monkeypatch):
    """install-menubar-agent writes a plist that runs `python -m lang_view menubar`
    with --pause-flag-file and --recent forwarded."""
    from lang_view.__main__ import main as cli_main

    monkeypatch.setattr("lang_view.launchd.is_launchctl_available", lambda: False)

    plist_path = tmp_path / "ai.test.menubar.plist"
    rc = cli_main([
        "install-menubar-agent",
        "--label", "ai.test.menubar",
        "--plist", str(plist_path),
        "--pause-flag-file", str(tmp_path / "p.flag"),
        "--recent", str(tmp_path / "lv.db"),
    ])
    assert rc == 0
    plist = plistlib.loads(plist_path.read_bytes())
    args = plist["ProgramArguments"]
    assert plist["Label"] == "ai.test.menubar"
    # Spot-check the watch is NOT what's launched — should be `menubar`.
    assert "menubar" in args
    assert "watch" not in args
    assert "--pause-flag-file" in args
    assert args[args.index("--pause-flag-file") + 1] == str(tmp_path / "p.flag")
    assert "--recent" in args


def test_install_agent_cli_omits_default_flags(tmp_path, monkeypatch):
    """Flags left at their defaults should NOT clutter ProgramArguments."""
    from lang_view.__main__ import main as cli_main

    monkeypatch.setattr("lang_view.launchd.is_launchctl_available", lambda: False)

    plist_path = tmp_path / "ai.bare.plist"
    rc = cli_main([
        "install-agent",
        "--label", "ai.bare",
        "--plist", str(plist_path),
    ])
    assert rc == 0
    plist = plistlib.loads(plist_path.read_bytes())
    args = plist["ProgramArguments"]
    # No watch flags should be forwarded when nothing differs from defaults.
    assert "--lang" not in args
    assert "--interval" not in args
    assert "--active-window" not in args
    assert "--app-allow" not in args
    assert "--title-allow" not in args
    assert "--group-lines" not in args
    assert "--frames-dir" not in args

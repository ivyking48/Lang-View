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

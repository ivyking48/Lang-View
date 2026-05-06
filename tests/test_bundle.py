import os
import plistlib
import stat
import subprocess
from types import SimpleNamespace

from lang_view.bundle import (
    DEFAULT_BUNDLE_ID,
    DEFAULT_BUNDLE_NAME,
    bundle_executable,
    codesign_bundle,
    default_bundle_path,
    lsregister_bundle,
    render_info_plist,
    render_launcher_script,
    write_app_bundle,
)


def test_render_info_plist_has_required_keys():
    plist = plistlib.loads(render_info_plist())
    assert plist["CFBundleIdentifier"] == DEFAULT_BUNDLE_ID
    assert plist["CFBundleName"] == DEFAULT_BUNDLE_NAME
    assert plist["CFBundleExecutable"] == "lang-view"
    assert plist["CFBundlePackageType"] == "APPL"
    # LSUIElement keeps the bundle out of Dock/App Switcher.
    assert plist["LSUIElement"] is True


def test_render_info_plist_custom_id_and_name():
    plist = plistlib.loads(render_info_plist(
        bundle_id="ai.test.foo", bundle_name="FooBar"))
    assert plist["CFBundleIdentifier"] == "ai.test.foo"
    assert plist["CFBundleName"] == "FooBar"


def test_render_launcher_script_invokes_python_module():
    script = render_launcher_script("/usr/bin/python3")
    assert script.startswith("#!/bin/bash")
    assert "/usr/bin/python3 -m lang_view" in script


def test_render_launcher_script_does_not_exec():
    """Parent bash MUST stay alive so macOS TCC attributes ScreenCapture
    to the bundle (not the unsigned python binary). Using `exec` would
    swap the running image and lose the bundle's identity."""
    script = render_launcher_script("/usr/bin/python3")
    # Should NOT start the launch line with `exec`.
    assert "exec /usr/bin/python3" not in script
    # Should wait on the child and forward signals.
    assert "wait $child" in script
    assert "trap" in script


def test_render_launcher_script_quotes_paths_with_spaces():
    script = render_launcher_script("/path with space/python")
    assert "'/path with space/python'" in script


def test_write_app_bundle_creates_required_layout(tmp_path):
    bundle = tmp_path / "MyApp.app"
    write_app_bundle(bundle, python_executable="/usr/bin/python3",
                     bundle_id="ai.test.x", bundle_name="MyApp")
    assert (bundle / "Contents" / "Info.plist").exists()
    assert (bundle / "Contents" / "MacOS" / "lang-view").exists()
    assert (bundle / "Contents" / "Resources").is_dir()
    assert (bundle / "Contents" / "PkgInfo").read_text() == "APPL????"


def test_write_app_bundle_launcher_is_executable(tmp_path):
    bundle = tmp_path / "MyApp.app"
    write_app_bundle(bundle, python_executable="/usr/bin/python3")
    launcher = bundle / "Contents" / "MacOS" / "lang-view"
    mode = launcher.stat().st_mode
    assert mode & stat.S_IXUSR
    assert mode & stat.S_IXGRP
    assert mode & stat.S_IXOTH


def test_write_app_bundle_info_plist_round_trip(tmp_path):
    bundle = tmp_path / "MyApp.app"
    write_app_bundle(bundle, python_executable="/usr/bin/python3",
                     bundle_id="ai.test.bar", bundle_name="Bar")
    info = plistlib.loads((bundle / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == "ai.test.bar"
    assert info["CFBundleName"] == "Bar"


def test_write_app_bundle_is_idempotent(tmp_path):
    bundle = tmp_path / "MyApp.app"
    write_app_bundle(bundle, python_executable="/usr/bin/python3")
    # Touch the launcher to detect overwrite.
    launcher = bundle / "Contents" / "MacOS" / "lang-view"
    launcher.write_text("# stale\n")
    write_app_bundle(bundle, python_executable="/usr/bin/python3")
    assert "/usr/bin/python3 -m lang_view" in launcher.read_text()


def test_default_bundle_path_in_user_applications():
    p = default_bundle_path()
    assert p.name == "Lang-View.app"
    assert "Applications" in p.parts


def test_bundle_executable_resolves_from_info_plist(tmp_path):
    bundle = tmp_path / "MyApp.app"
    write_app_bundle(bundle, python_executable="/usr/bin/python3",
                     executable_name="my-launcher")
    exe = bundle_executable(bundle)
    assert exe.name == "my-launcher"
    assert exe.parent.name == "MacOS"


def _ok(*_):
    return SimpleNamespace(returncode=0, stdout="", stderr="")


def _fail(*_):
    return SimpleNamespace(returncode=1, stdout="", stderr="boom")


def test_codesign_bundle_invokes_adhoc_sign(tmp_path):
    calls = []

    def runner(cmd):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    bundle = tmp_path / "MyApp.app"
    assert codesign_bundle(bundle, runner=runner) is True
    # Ad-hoc signing uses "-" as the identity and must walk into
    # the bundle (--deep) and overwrite any prior signature (--force).
    assert calls == [["codesign", "--force", "--deep", "--sign", "-", str(bundle)]]


def test_codesign_bundle_returns_false_on_failure(tmp_path):
    bundle = tmp_path / "MyApp.app"
    assert codesign_bundle(bundle, runner=_fail) is False


def test_codesign_bundle_handles_missing_binary(tmp_path):
    def runner(cmd):
        raise FileNotFoundError("no codesign on PATH")

    bundle = tmp_path / "MyApp.app"
    assert codesign_bundle(bundle, runner=runner) is False


def test_lsregister_bundle_uses_launch_services_helper(tmp_path):
    calls = []

    def runner(cmd):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    bundle = tmp_path / "MyApp.app"
    assert lsregister_bundle(bundle, runner=runner) is True
    assert len(calls) == 1
    cmd = calls[0]
    # lsregister lives inside the LaunchServices framework, not on PATH;
    # passing the absolute path is what makes this work in launchd
    # contexts where PATH is minimal.
    assert cmd[0].endswith("/lsregister")
    assert cmd[1:] == ["-f", str(bundle)]


def test_lsregister_bundle_returns_false_on_failure(tmp_path):
    bundle = tmp_path / "MyApp.app"
    assert lsregister_bundle(bundle, runner=_fail) is False

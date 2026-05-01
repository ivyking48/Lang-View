from lang_view.menubar import MenubarState
from lang_view.worker import PauseSwitch


def test_state_starts_running_with_no_recents():
    s = MenubarState()
    assert s.is_paused is False
    assert s.recent() == []


def test_append_captures_records_in_reverse_chronological_order():
    s = MenubarState(max_recent=3)
    s.append({"lang": "ko", "text": "안녕"})
    s.append({"lang": "ja", "text": "今日"})
    s.append({"lang": "ja", "text": "天気"})
    recent = s.recent()
    assert [r["text"] for r in recent] == ["天気", "今日", "안녕"]


def test_append_respects_max_size():
    s = MenubarState(max_recent=2)
    for text in ["a", "b", "c", "d"]:
        s.append({"lang": "ko", "text": text})
    assert [r["text"] for r in s.recent()] == ["d", "c"]


def test_append_ignores_blank_records():
    s = MenubarState()
    s.append({})
    s.append({"lang": "ko", "text": ""})
    s.append(None)
    assert s.recent() == []


def test_toggle_pause_uses_injected_switch():
    pause = PauseSwitch()
    s = MenubarState(pause_switch=pause)
    assert s.is_paused is False
    s.toggle_pause()
    assert s.is_paused is True
    assert pause.is_paused()


def test_toggle_pause_returns_none_without_switch():
    s = MenubarState()
    assert s.toggle_pause() is None


def test_listeners_are_notified_on_append_and_toggle():
    pause = PauseSwitch()
    s = MenubarState(pause_switch=pause)
    fired = []
    s.add_listener(lambda state: fired.append(("update", state.is_paused)))
    s.append({"lang": "ko", "text": "안녕"})
    s.toggle_pause()
    assert len(fired) == 2
    assert fired[1] == ("update", True)


def test_listener_exceptions_are_swallowed():
    s = MenubarState()
    s.add_listener(lambda _s: (_ for _ in ()).throw(RuntimeError("nope")))
    s.append({"lang": "ko", "text": "안녕"})  # Should not raise.
    assert len(s.recent()) == 1

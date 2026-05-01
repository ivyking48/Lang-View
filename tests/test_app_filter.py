from lang_view.app_filter import AppFilter


def test_empty_filter_allows_everything():
    f = AppFilter()
    assert f.allows("Safari")
    assert f.allows(None)


def test_allowlist_only_matches_listed_apps():
    f = AppFilter(allow=["Safari", "Chrome"])
    assert f.allows("Safari")
    assert f.allows("Google Chrome")
    assert not f.allows("Slack")


def test_allowlist_blocks_unknown_app():
    f = AppFilter(allow=["Safari"])
    assert not f.allows(None)


def test_blocklist_blocks_listed_apps():
    f = AppFilter(block=["1Password"])
    assert f.allows("Safari")
    assert not f.allows("1Password 7")


def test_allowlist_takes_precedence_over_blocklist():
    f = AppFilter(allow=["Safari"], block=["Safari"])
    assert f.allows("Safari")
    assert not f.allows("Chrome")


def test_match_is_case_insensitive():
    f = AppFilter(allow=["safari"])
    assert f.allows("SAFARI")


def test_parse_handles_csv_strings():
    f = AppFilter.parse("Safari, Chrome", " 1Password ,Slack")
    assert f.allow == ["safari", "chrome"]
    assert f.block == ["1password", "slack"]


def test_parse_ignores_blank_entries():
    f = AppFilter.parse(",,Safari,,")
    assert f.allow == ["safari"]

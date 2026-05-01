import logging

from lang_view.enrich import EnrichmentPipeline, build_pipeline


class FakeEnricher:
    name = "fake"

    def __init__(self, applies, payload, raise_on=None):
        self._applies = applies
        self._payload = payload
        self._raise_on = raise_on

    def applies_to(self, lang):
        return lang in self._applies

    def enrich(self, text, lang):
        if self._raise_on and self._raise_on in text:
            raise RuntimeError("boom")
        return dict(self._payload)


def test_pipeline_merges_outputs():
    p = EnrichmentPipeline([
        FakeEnricher({"ja"}, {"a": 1}),
        FakeEnricher({"ja", "ko"}, {"b": 2}),
    ])
    assert p.enrich("text", "ja") == {"a": 1, "b": 2}
    assert p.enrich("text", "ko") == {"b": 2}


def test_pipeline_swallows_enricher_errors(caplog):
    p = EnrichmentPipeline([
        FakeEnricher({"ja"}, {"a": 1}, raise_on="bad"),
        FakeEnricher({"ja"}, {"b": 2}),
    ])
    with caplog.at_level(logging.WARNING):
        out = p.enrich("bad", "ja")
    assert out == {"b": 2}
    assert any("Enricher" in rec.message for rec in caplog.records)


def test_pipeline_returns_empty_when_nothing_applies():
    p = EnrichmentPipeline([FakeEnricher({"ja"}, {"a": 1})])
    assert p.enrich("text", "ko") == {}


def test_build_pipeline_empty_spec():
    assert len(build_pipeline("")) == 0
    assert len(build_pipeline(None)) == 0


def test_build_pipeline_unknown_token_warns(caplog):
    with caplog.at_level(logging.WARNING):
        p = build_pipeline("notarealthing")
    assert len(p) == 0
    assert any("Unknown" in rec.message for rec in caplog.records)

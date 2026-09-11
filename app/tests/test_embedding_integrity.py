"""
WHAT:
    Tests the defences around the one failure that would otherwise be
    completely silent: a vector of the wrong width, or from the wrong
    model, sitting in product_embeddings.

THE RISK, STATED PLAINLY:
    The catalogue was migrated to azure:text-embedding-3-small:1536, and
    the Atlas index is built at 1536 dimensions. Zatch's OWN
    change-stream pipeline re-embeds a product whenever a seller edits
    it - with the old 384-dim model, writing no tag. It has not fired
    only because nothing has been edited since the migration.

    Atlas cannot index a vector whose width does not match the index, so
    the affected product simply stops appearing in semantic search.
    Nothing raises. Nothing logs. The search keeps working and returns
    one fewer result.

WHY THE TESTS FAKE THE DATABASE:
    Proving the audit REPORTS a clean collection is worthless - the live
    collection is clean, so a test against it passes whether the detector
    works or not. What has to be proved is that it DETECTS, and the app's
    credential is read-only by design, so the poisoned document cannot be
    created for real. The fake below implements only the four queries
    these functions actually issue, so the query logic is genuinely under
    test rather than stubbed past.
"""

import pytest

from app.agent import embeddings as embeddings_module
from app.agent.embeddings import (
    EmbeddingDimensionMismatch,
    expected_dimensions,
    validate_dimensions,
)
from app.repos import products_repo

TAG = "azure:text-embedding-3-small:1536"
DIMS = 1536


def doc(oid, width=DIMS, tag=TAG):
    row = {"_id": oid, "embedding": [0.0] * width}
    if tag is not None:
        row["embeddingModel"] = tag
    return row


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def __aiter__(self):
        async def gen():
            for row in self._rows:
                yield row
        return gen()


class FakeCollection:
    """Implements exactly the four query shapes the integrity checks use.
    Anything else raises, so a query that quietly changed shape fails the
    test rather than silently matching nothing."""

    def __init__(self, rows):
        self.rows = rows

    def _match(self, query):
        if query == {}:
            return list(self.rows)
        if "embeddingModel" in query:
            clause = query["embeddingModel"]
            if "$ne" in clause:
                # A MISSING FIELD MATCHES $ne in MongoDB, which is the
                # whole reason the guard can catch an untagged rogue.
                return [r for r in self.rows
                        if r.get("embeddingModel") != clause["$ne"]]
            if clause.get("$exists") is False:
                return [r for r in self.rows if "embeddingModel" not in r]
        if "embedding" in query:
            size = query["embedding"]["$not"]["$size"]
            return [r for r in self.rows if len(r["embedding"]) != size]
        raise AssertionError(f"unsupported query shape: {query}")

    def find(self, query, projection=None):
        return FakeCursor(self._match(query))

    def aggregate(self, pipeline):
        """The $vectorSearch stage. Empty on purpose - what these tests
        assert is whether the GUARD let the search proceed, not what
        Atlas would have ranked."""
        return FakeCursor([])

    async def find_one(self, query, projection=None):
        found = self._match(query)
        return found[0] if found else None

    async def count_documents(self, query):
        return len(self._match(query))


class FakeDatabase:
    def __init__(self, rows):
        self.collection = FakeCollection(rows)

    def __getitem__(self, name):
        return self.collection


@pytest.fixture
def catalogue(monkeypatch):
    """Installs a fake product_embeddings collection."""

    def _install(rows):
        monkeypatch.setattr(
            products_repo, "get_database", lambda: FakeDatabase(rows)
        )
        return rows

    return _install


# ── Validation, at the source ────────────────────────────────────────

class TestNothingCanProduceAWrongWidthVector:
    """The write side. embed_texts validates before returning, so no
    producer in this repo can hand a bad vector to anything."""

    def test_correct_width_passes(self):
        validate_dimensions([[0.0] * DIMS, [1.0] * DIMS])

    def test_the_old_model_width_is_refused(self):
        """The exact shape of the incident: 384 into a 1536 collection."""
        with pytest.raises(EmbeddingDimensionMismatch) as exc:
            validate_dimensions([[0.0] * DIMS, [0.0] * 384])
        assert "384" in str(exc.value)
        assert "1536" in str(exc.value)

    def test_it_names_the_position_not_just_the_count(self):
        """With a batch of 64, "one bad vector" and "the model changed
        width" need different responses, and the positions say which."""
        with pytest.raises(EmbeddingDimensionMismatch) as exc:
            validate_dimensions([[0.0] * DIMS, [0.0] * DIMS, [0.0] * 384])
        assert "#2" in str(exc.value)

    def test_a_missing_vector_is_refused(self):
        with pytest.raises(EmbeddingDimensionMismatch):
            validate_dimensions([None])

    def test_an_empty_batch_is_fine(self):
        validate_dimensions([])

    def test_the_expected_width_follows_the_backend(self, monkeypatch):
        """The tag and the width describe the same model, so they are
        read from the same setting - a guard whose halves disagree would
        certify the state it exists to catch."""
        assert expected_dimensions() == DIMS

        settings = embeddings_module.get_settings()
        monkeypatch.setattr(settings, "embedding_backend", "local")
        monkeypatch.setattr(settings, "local_embedding_dimensions", 384)
        assert expected_dimensions() == 384


# ── Detection, on what is already stored ─────────────────────────────

class TestTheAuditFindsWhatASampleWouldMiss:
    async def test_a_clean_collection_is_reported_clean(self, catalogue):
        catalogue([doc(i) for i in range(143)])
        audit = await products_repo.audit_embeddings()
        assert audit.level == "ok"
        assert audit.healthy
        assert audit.total == 143 and audit.ok == 143

    async def test_one_poisoned_document_among_143(self, catalogue):
        """THE WHOLE POINT. The old guard sampled ONE document, so it
        found this with probability 1/143. The failure is exactly one
        document, because it takes exactly one seller editing one
        product."""
        rows = [doc(i) for i in range(142)]
        rows.append(doc("rogue", width=384, tag=None))
        catalogue(rows)

        audit = await products_repo.audit_embeddings()
        assert audit.level == "degraded"
        assert not audit.healthy
        assert audit.ok == 142
        assert [r["_id"] for r in audit.wrong_width] == ["rogue"]
        assert audit.wrong_tag[0]["tag"] == "(untagged)"

    async def test_a_right_width_vector_from_the_wrong_model(self, catalogue):
        """Width alone is not enough: a 1536-dim vector from a different
        model indexes perfectly and returns plausible noise."""
        rows = [doc(i) for i in range(10)]
        rows.append(doc("foreign", width=DIMS, tag="openai:text-embedding-ada-002:1536"))
        catalogue(rows)

        audit = await products_repo.audit_embeddings()
        assert audit.level == "degraded"
        assert not audit.wrong_width
        assert [r["_id"] for r in audit.wrong_tag] == ["foreign"]

    async def test_a_wholly_foreign_catalogue_is_broken_not_degraded(
        self, catalogue
    ):
        """Some documents wrong is contamination; all of them wrong means
        the catalogue belongs to another model, and the two want opposite
        responses."""
        catalogue([doc(i, width=384, tag=None) for i in range(20)])
        audit = await products_repo.audit_embeddings()
        assert audit.level == "broken"
        assert audit.ok == 0

    async def test_an_empty_collection_is_its_own_state(self, catalogue):
        catalogue([])
        audit = await products_repo.audit_embeddings()
        assert audit.level == "empty"
        assert not audit.healthy

    async def test_an_operator_assertion_covers_an_untagged_catalogue(
        self, catalogue, monkeypatch
    ):
        """LEGACY_EMBEDDING_ASSUMED, honoured here exactly as at search
        time - otherwise the audit would contradict the search."""
        settings = products_repo.get_settings()
        monkeypatch.setattr(settings, "legacy_embedding_assumed", TAG)
        catalogue([doc(i, tag=None) for i in range(5)])

        audit = await products_repo.audit_embeddings()
        assert audit.level == "ok"


class TestTheCheapStatusAgreesWithTheDeepScan:
    """/health cannot walk the collection on every poll, so it counts
    instead. Two implementations of one question must not disagree."""

    @pytest.mark.parametrize("rows,expected", [
        ([doc(i) for i in range(10)], "ok"),
        ([], "empty"),
        ([doc(i) for i in range(9)] + [doc("r", width=384, tag=None)], "degraded"),
        ([doc(i, width=384, tag=None) for i in range(4)], "broken"),
    ])
    async def test_both_reach_the_same_verdict(self, catalogue, rows, expected):
        catalogue(rows)
        status = await products_repo.embedding_status()
        audit = await products_repo.audit_embeddings()
        assert status["level"] == expected
        assert audit.level == expected

    async def test_the_count_is_reported(self, catalogue):
        catalogue([doc(i) for i in range(9)] + [doc("r", width=384, tag=None)])
        status = await products_repo.embedding_status()
        assert status["total"] == 10
        assert status["contaminated"] == 1


# ── The search itself ────────────────────────────────────────────────

class TestSearchDegradesLoudlyRatherThanSilently:
    async def test_contamination_does_not_take_search_down(
        self, catalogue, monkeypatch
    ):
        """ONE edited product must not become a total outage. The other
        142 comparisons are valid, and Atlas has already dropped the bad
        vector from the index - so the search runs and the incident is
        made loud instead."""
        rows = [doc(i) for i in range(142)]
        rows.append(doc("rogue", width=384, tag=None))
        catalogue(rows)

        seen = {}

        def capture(event, **fields):
            seen[event] = fields

        monkeypatch.setattr(products_repo.logger, "error", capture)

        async def fake_embed(text):
            return [0.0] * DIMS

        monkeypatch.setattr(products_repo, "embed_query", fake_embed)

        # The vector stage is not part of what this asserts; an empty
        # result is enough to prove the guard let the search through.
        await products_repo.search_products_semantically("something cosy")

        assert "embedding_collection_contaminated" in seen, (
            "contamination passed without a word - the exact silence this "
            "whole feature exists to end"
        )
        finding = seen["embedding_collection_contaminated"]
        assert finding["contaminated"] == 1
        assert finding["total"] == 143
        assert finding["example_width"] == 384

    async def test_a_wholly_foreign_catalogue_still_refuses(self, catalogue):
        """Unchanged behaviour: comparing across two vector spaces
        returns ranked, plausible noise, so it must not run at all."""
        from app.agent.embeddings import EmbeddingModelMismatch

        catalogue([doc(i, tag="something:else:384") for i in range(5)])
        with pytest.raises(EmbeddingModelMismatch):
            await products_repo.search_products_semantically("cosy")


class TestTheReportedCountIsTheTrueCount:
    """A REPORTING BUG CAUGHT BY RUNNING IT. The sample lists are capped
    so a wholly broken collection does not print 143 ids - and reading
    their LENGTH as the count made the audit report "20 wrong" when all
    143 were. A number that under-reports the size of an incident is
    worse than no number, because it gets believed."""

    async def test_the_count_is_not_capped_by_the_sample(self, catalogue):
        catalogue([doc(i, width=384, tag=None) for i in range(143)])
        audit = await products_repo.audit_embeddings(limit_reported=20)

        assert audit.wrong_width_count == 143
        assert audit.wrong_tag_count == 143
        assert len(audit.wrong_width) == 20, "the sample should stay capped"
        assert audit.truncated
        assert "143 wrong-width" in audit.summary

    async def test_a_small_problem_is_not_marked_truncated(self, catalogue):
        catalogue([doc(i) for i in range(10)] + [doc("r", width=384, tag=None)])
        audit = await products_repo.audit_embeddings()
        assert audit.wrong_width_count == 1
        assert not audit.truncated

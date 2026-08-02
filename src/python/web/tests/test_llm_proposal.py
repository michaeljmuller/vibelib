from web.ingest.llm import (
    Adjudication,
    BookDecision,
    BookUpdate,
    SeriesRef,
    to_proposal,
)


def _adjudication(book: BookDecision, acquired_on: str | None = None) -> Adjudication:
    return Adjudication(
        book=book,
        authors=[],
        narrators=[],
        pseudonym_proposals=[],
        acquired_on=acquired_on,
        metadata_insufficient=False,
        confidence=0.95,
        notes="",
    )


class TestToProposalUpdate:
    def test_link_with_update(self):
        adj = _adjudication(
            BookDecision(
                link_book_id=1077,
                update=BookUpdate(
                    title="Heretical Fishing",
                    series=SeriesRef(link_series_id=166, create_name=None),
                    series_position=1,
                ),
                create=None,
            )
        )
        p = to_proposal(adj)
        assert p["book"]["link"] == 1077
        assert p["book"]["update"]["title"] == "Heretical Fishing"
        assert p["book"]["update"]["sort_title"] == "Heretical Fishing"
        assert p["book"]["update"]["series"] == {"link": 166}
        assert p["book"]["update"]["series_position"] == 1

    def test_link_without_update_has_no_update_key(self):
        adj = _adjudication(
            BookDecision(link_book_id=63, update=None, create=None)
        )
        p = to_proposal(adj)
        assert p["book"] == {"link": 63}

    def test_empty_update_is_dropped(self):
        adj = _adjudication(
            BookDecision(
                link_book_id=63,
                update=BookUpdate(title=None, series=None, series_position=None),
                create=None,
            )
        )
        p = to_proposal(adj)
        assert "update" not in p["book"]


class TestToProposalAcquired:
    """The acquisition date is the reviewer's to set, not the model's to guess,
    so it only appears in a proposal when a correction put it there."""

    def test_absent_when_the_model_gives_none(self):
        adj = _adjudication(BookDecision(link_book_id=63, update=None, create=None))
        assert "acquired_on" not in to_proposal(adj)

    def test_carried_through_when_given(self):
        adj = _adjudication(
            BookDecision(link_book_id=63, update=None, create=None),
            acquired_on="2026-07-27",
        )
        assert to_proposal(adj)["acquired_on"] == "2026-07-27"

    def test_junk_is_dropped_rather_than_shown(self):
        adj = _adjudication(
            BookDecision(link_book_id=63, update=None, create=None),
            acquired_on="last Tuesday",
        )
        assert "acquired_on" not in to_proposal(adj)

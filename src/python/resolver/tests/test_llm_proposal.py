from resolver.llm import (
    Adjudication,
    BookDecision,
    BookUpdate,
    SeriesRef,
    to_proposal,
)


def _adjudication(book: BookDecision) -> Adjudication:
    return Adjudication(
        book=book,
        authors=[],
        narrators=[],
        pseudonym_proposals=[],
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

"""The review card's judgment, ported from the CLI's render_card tests.

Same cases, asserted against the structured rows the browser now renders. The
one worth reading twice is the series pair at the bottom: a proposed CREATE
whose name already exists must be shown as a LINK, because that is what apply
will actually do with it, and a CREATE that merely resembles an existing series
must carry the warning -- that near miss is how a series gets split in two.
"""

from web.ingest.summary import proposal_rows, raw_rows, review_reason


def _epub_meta(**overrides):
    base = {
        "asset_type": "epub",
        "title": "All the Weyrs of Pern",
        "authors": ["McCaffrey, Anne"],
        "series": None,
        "series_position": None,
        "published_date": "2012",
        "language": "en",
        "narrators": [],
        "s3_key": "All the Weyrs of Pern - Anne McCaffrey.epub",
    }
    base.update(overrides)
    return base


def _create_proposal(**overrides):
    base = {
        "book": {
            "create": {
                "title": "All the Weyrs of Pern",
                "sort_title": "All the Weyrs of Pern",
                "series": {"create": {"name": "Pern", "sort_name": "Pern"}},
                "series_position": 11,
                "publication_date": "1991-01-01",
                "language": "en",
            }
        },
        "authors": [
            {"create": {"name": "Anne McCaffrey", "sort_name": "McCaffrey, Anne",
                        "disambiguator": None}, "raw_name": "McCaffrey, Anne"}
        ],
        "narrators": [],
        "pseudonyms": [],
    }
    base.update(overrides)
    return base


def _find(rows, label):
    return next(r for r in rows if r["label"] == label)


def _labels(rows):
    return [r["label"] for r in rows]


class TestReviewReason:
    def test_low_confidence(self):
        assert "0.85" in review_reason(_create_proposal(), 0.85)
        assert "unsure" in review_reason(_create_proposal(), 0.85)

    def test_pseudonym_wins(self):
        p = _create_proposal(
            pseudonyms=[{"pseudonym_name": "James S. A. Corey",
                         "real_person_names": ["Daniel Abraham", "Ty Franck"]}]
        )
        assert "pseudonym" in review_reason(p, 0.95)

    def test_book_update_wins(self):
        p = {"book": {"link": 1077, "update": {"series_position": 1}}}
        assert "existing book" in review_reason(p, 0.99)

    def test_confident_proposal_reads_as_routine_not_as_a_problem(self):
        # A 1.0 proposal is on a card like every other one, so its line must
        # tell the reviewer it is routine rather than implying something is
        # wrong with it.
        assert "nothing unusual" in review_reason(_create_proposal(), 1.0)

    def test_no_line_claims_anything_is_auto_committed(self):
        # These strings are the last place the old auto-commit behaviour could
        # survive as a claim to the user after it stopped being true.
        for conf in (0.2, 0.85, 0.95, 1.0):
            for p in (_create_proposal(),
                      {"book": {"link": 1, "update": {"series_position": 1}}},
                      _create_proposal(pseudonyms=[{"pseudonym_name": "X",
                                                    "real_person_names": ["Y"]}])):
                assert "auto" not in review_reason(p, conf).lower()


class TestRawRows:
    def test_epub_fields(self):
        rows = raw_rows(_epub_meta())
        assert _find(rows, "Title")["text"] == "All the Weyrs of Pern"
        assert _find(rows, "Authors")["text"] == "McCaffrey, Anne"
        assert _find(rows, "Date")["text"] == "2012 (edition)"
        assert _find(rows, "File")["text"].endswith(".epub")

    def test_missing_authors_say_so(self):
        assert _find(raw_rows(_epub_meta(authors=[])), "Authors")["text"] == "(none)"

    def test_m4b_fields(self):
        rows = raw_rows(
            {
                "asset_type": "m4b",
                "title": "Project Hail Mary",
                "authors": ["Andy Weir"],
                "narrators": ["Ray Porter"],
                "album": "Project Hail Mary",
                "date": "2021",
                "s3_key": "Project Hail Mary.m4b",
            }
        )
        assert _find(rows, "Narrators")["text"] == "Ray Porter"
        assert _find(rows, "Album")["text"] == "Project Hail Mary"
        assert "Series" not in _labels(rows)  # m4bs carry no series column


class TestProposalRows:
    def test_create_book(self):
        rows = proposal_rows(_create_proposal(), {})
        book = _find(rows, "Book")
        assert book["verb"] == "create"
        assert book["text"] == "“All the Weyrs of Pern”"
        assert _find(rows, "Position")["text"] == "11"
        assert _find(rows, "Published")["text"] == "1991-01-01"
        assert _find(rows, "Language")["text"] == "en"
        assert _find(rows, "Author")["verb"] == "create"

    def test_link_book_shows_name(self):
        proposal = {"book": {"link": 63}, "authors": [], "narrators": [], "pseudonyms": []}
        row = _find(proposal_rows(proposal, {"books": {63: "Backyard Starship"}}), "Book")
        assert row["verb"] == "link"
        assert row["text"] == "#63 “Backyard Starship”"

    def test_link_book_without_name_falls_back_to_id(self):
        proposal = {"book": {"link": 63}, "authors": [], "narrators": [], "pseudonyms": []}
        assert _find(proposal_rows(proposal, {}), "Book")["text"] == "#63"

    def test_link_series_shows_name(self):
        p = _create_proposal()
        p["book"]["create"]["series"] = {"link": 12}
        rows = proposal_rows(p, {"series": {12: "The Expanse"}})
        assert _find(rows, "Series") == {
            "label": "Series", "verb": "link", "text": "#12 “The Expanse”", "warning": "",
        }
        assert _find(rows, "Position")["text"] == "11"

    def test_pseudonym_row(self):
        p = _create_proposal(
            pseudonyms=[{"pseudonym_name": "James S. A. Corey",
                         "real_person_names": ["Daniel Abraham", "Ty Franck"]}]
        )
        row = _find(proposal_rows(p, {}), "Pseudonym")
        assert row["text"] == "James S. A. Corey → Daniel Abraham + Ty Franck"

    def test_narrator_link_keeps_the_raw_name(self):
        p = _create_proposal(narrators=[{"link": 7, "raw_name": "Porter, Ray"}])
        row = _find(proposal_rows(p, {"people": {7: "Ray Porter"}}), "Narrator")
        assert row["verb"] == "link"
        assert row["text"] == "#7 Ray Porter  (raw: Porter, Ray)"

    def test_person_create_shows_disambiguator(self):
        p = _create_proposal(
            authors=[{"create": {"name": "Ian Banks", "disambiguator": "the historian"},
                      "raw_name": "Ian Banks"}]
        )
        assert "the historian" in _find(proposal_rows(p, {}), "Author")["text"]

    def test_link_with_updates(self):
        proposal = {
            "book": {
                "link": 1077,
                "update": {"title": "Heretical Fishing", "sort_title": "Heretical Fishing",
                           "series_position": 1},
            },
            "authors": [], "narrators": [], "pseudonyms": [],
        }
        rows = proposal_rows(proposal, {"books": {1077: "Heretical Fishing"}})
        assert _find(rows, "Book")["text"] == "#1077 “Heretical Fishing”"
        assert _find(rows, "Retitle")["text"] == "“Heretical Fishing”"
        assert _find(rows, "Reposition")["text"] == "1"

    def test_series_create_that_already_exists_renders_as_link(self):
        rows = proposal_rows(
            _create_proposal(),
            {"series_match": {"Pern": {"id": 75, "name": "Dragonriders of Pern"}}},
        )
        row = _find(rows, "Series")
        assert row["verb"] == "link"
        assert row["text"] == "#75 “Dragonriders of Pern”  (matched by name)"

    def test_series_create_warns_on_near_match(self):
        rows = proposal_rows(
            _create_proposal(),
            {"series_near": {"Pern": [{"id": 75, "name": "Dragonriders of Pern"}]}},
        )
        row = _find(rows, "Series")
        assert row["verb"] == "create"
        assert "near-match: #75 “Dragonriders of Pern”" in row["warning"]

    def test_series_create_with_no_match_is_plain_create(self):
        row = _find(proposal_rows(_create_proposal(), {}), "Series")
        assert row["verb"] == "create"
        assert row["warning"] == ""

    def test_update_series_create_also_resolved(self):
        proposal = {
            "book": {
                "link": 1077,
                "update": {"series": {"create": {"name": "Pern", "sort_name": "Pern"}}},
            },
            "authors": [], "narrators": [], "pseudonyms": [],
        }
        rows = proposal_rows(
            proposal, {"series_match": {"Pern": {"id": 75, "name": "Dragonriders of Pern"}}}
        )
        row = _find(rows, "Reassign series")
        assert row["verb"] == "link"
        assert "#75 “Dragonriders of Pern”" in row["text"]

    def test_interstitial_position(self):
        p = _create_proposal()
        p["book"]["create"]["series_position"] = None
        assert _find(proposal_rows(p, {}), "Position")["text"] == "none (interstitial)"


class TestNothingIsWrittenBeforeAccept:
    """The rule the whole page rests on, and the reason cancel needs no route.

    Asserted against the modules rather than a scenario, because the failure to
    guard against is a future caller reintroducing a write on the read path --
    at which point an abandoned proposal starts leaving something behind, and
    every question the review queue used to raise comes back with it.
    """

    def test_resolve_cannot_reach_the_catalog_or_the_log(self):
        import inspect

        from web.ingest import resolve

        source = inspect.getsource(resolve)
        # apply_proposal is the only function that writes books/people/series;
        # log_resolution is the only one that records that it happened.
        assert not hasattr(resolve, "apply")
        assert "apply_proposal" not in source
        assert "log_resolution" not in source
        assert "INSERT" not in source.upper()

    def test_propose_takes_no_flag_that_could_apply_it(self):
        import inspect

        from web.ingest import resolve

        params = inspect.signature(resolve.propose).parameters
        for gone in ("auto_apply", "dry_run", "commit", "apply"):
            assert gone not in params

    def test_accept_is_the_only_route_that_writes(self):
        import inspect

        from web.ingest import api

        assert "apply_proposal" not in inspect.getsource(api.api_resolve)
        assert "apply_proposal" not in inspect.getsource(api.api_revise)
        assert "apply_proposal" in inspect.getsource(api.api_accept)

    def test_there_is_no_cancel_route_to_get_wrong(self):
        # Cancel is the browser dropping an object. A route would imply there
        # was something on this side to undo.
        from web.ingest import api

        paths = {r.path for r in api.router.routes}
        assert not any("cancel" in p or "reject" in p for p in paths)


class TestListBIsJustAQuery:
    def test_unresolved_does_not_consult_resolutions(self):
        # An abandoned proposal leaves no trace, so "has a row, has no book" is
        # the whole definition -- and the asset comes straight back, unchanged.
        import inspect

        from web.ingest import store

        sql = inspect.getsource(store.get_unresolved).upper()
        assert "JOIN RESOLUTIONS" not in sql
        assert "FROM RESOLUTIONS" not in sql

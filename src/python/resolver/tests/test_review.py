from resolver.review import render_card, review_reason


def _resolution(**overrides):
    base = {
        "id": 20,
        "asset_type": "epub",
        "asset_id": 74,
        "confidence": 0.85,
        "notes": "exact numbering can vary by convention",
    }
    base.update(overrides)
    return base


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


class TestReviewReason:
    def test_low_confidence(self):
        assert "confidence 0.85" in review_reason(_create_proposal(), 0.85)

    def test_pseudonym_wins(self):
        p = _create_proposal(
            pseudonyms=[{"pseudonym_name": "James S. A. Corey",
                         "real_person_names": ["Daniel Abraham", "Ty Franck"]}]
        )
        assert "pseudonym" in review_reason(p, 0.95)


class TestRenderCard:
    def test_create_book_card(self):
        card = render_card(_resolution(), _epub_meta(), _create_proposal(), 3, 9)
        assert "#20 · epub:74" in card
        assert "3 of 9" in card
        assert "raw title    All the Weyrs of Pern" in card
        assert "raw authors  McCaffrey, Anne" in card
        assert "CREATE  “All the Weyrs of Pern”" in card
        assert " series    CREATE  “Pern”" in card
        assert " position  11" in card
        assert " published 1991-01-01" in card
        assert " language  en" in card
        assert "confidence 0.85" in card
        assert "needs review: confidence 0.85 below 0.90" in card
        assert "exact numbering" in card

    def test_link_book_card_shows_name(self):
        proposal = {"book": {"link": 63}, "authors": [], "narrators": [], "pseudonyms": []}
        card = render_card(
            _resolution(), _epub_meta(), proposal, 1, 1,
            names={"books": {63: "Backyard Starship"}},
        )
        assert "LINK    existing book #63 “Backyard Starship”" in card

    def test_link_book_card_without_name_falls_back_to_id(self):
        proposal = {"book": {"link": 63}, "authors": [], "narrators": [], "pseudonyms": []}
        card = render_card(_resolution(), _epub_meta(), proposal, 1, 1)
        assert "LINK    existing book #63" in card

    def test_link_series_shows_name(self):
        p = _create_proposal()
        p["book"]["create"]["series"] = {"link": 12}
        card = render_card(
            _resolution(), _epub_meta(), p, 1, 1,
            names={"series": {12: "The Expanse"}},
        )
        assert "LINK    existing series #12 “The Expanse”" in card
        assert " position  11" in card

    def test_pseudonym_line_and_reason(self):
        p = _create_proposal(
            pseudonyms=[{"pseudonym_name": "James S. A. Corey",
                         "real_person_names": ["Daniel Abraham", "Ty Franck"]}]
        )
        card = render_card(_resolution(confidence=0.93), _epub_meta(), p, 1, 1)
        assert "James S. A. Corey → Daniel Abraham + Ty Franck" in card
        assert "needs review: pseudonym link proposed" in card

    def test_m4b_card_shows_narrators(self):
        meta = {
            "asset_type": "m4b",
            "title": "Project Hail Mary",
            "authors": ["Andy Weir"],
            "narrators": ["Ray Porter"],
            "album": "Project Hail Mary",
            "date": "2021",
        }
        proposal = _create_proposal(
            narrators=[{"link": 7, "raw_name": "Porter, Ray"}]
        )
        card = render_card(
            _resolution(asset_type="m4b", asset_id=6), meta, proposal, 1, 1,
            names={"people": {7: "Ray Porter"}},
        )
        assert "raw narrator Ray Porter" in card
        assert "narrator  LINK    #7 Ray Porter  (raw: Porter, Ray)" in card

    def test_edited_flag(self):
        card = render_card(
            _resolution(), _epub_meta(), _create_proposal(), 1, 1, edited=True
        )
        assert "EDITED" in card

    def test_link_with_update_lines_and_reason(self):
        proposal = {
            "book": {
                "link": 1077,
                "update": {"title": "Heretical Fishing", "sort_title": "Heretical Fishing",
                           "series_position": 1},
            },
            "authors": [], "narrators": [], "pseudonyms": [],
        }
        card = render_card(
            _resolution(confidence=0.95), _epub_meta(), proposal, 1, 1,
            names={"books": {1077: "Heretical Fishing"}},
        )
        assert "LINK    existing book #1077 “Heretical Fishing”" in card
        assert " update    title → “Heretical Fishing”" in card
        assert " update    position → 1" in card
        assert "needs review: modifies an existing book" in card

    def test_series_create_that_already_exists_renders_as_link(self):
        # apply._resolve_series silently links a create whose name already
        # exists; the card must say so rather than claiming CREATE.
        card = render_card(
            _resolution(), _epub_meta(), _create_proposal(), 1, 1,
            names={"series_match": {"Pern": {"id": 75, "name": "Dragonriders of Pern"}}},
        )
        assert "LINK    existing series #75 “Dragonriders of Pern”  (matched by name)" in card
        assert "CREATE  “Pern”" not in card

    def test_series_create_warns_on_near_match(self):
        card = render_card(
            _resolution(), _epub_meta(), _create_proposal(), 1, 1,
            names={"series_near": {"Pern": [{"id": 75, "name": "Dragonriders of Pern"}]}},
        )
        assert " series    CREATE  “Pern”" in card
        assert "⚠ near-match: #75 “Dragonriders of Pern”" in card

    def test_series_create_with_no_match_is_plain_create(self):
        card = render_card(_resolution(), _epub_meta(), _create_proposal(), 1, 1)
        assert " series    CREATE  “Pern”" in card
        assert "near-match" not in card

    def test_update_series_create_also_resolved(self):
        proposal = {
            "book": {
                "link": 1077,
                "update": {"series": {"create": {"name": "Pern", "sort_name": "Pern"}}},
            },
            "authors": [], "narrators": [], "pseudonyms": [],
        }
        card = render_card(
            _resolution(), _epub_meta(), proposal, 1, 1,
            names={"series_match": {"Pern": {"id": 75, "name": "Dragonriders of Pern"}}},
        )
        assert " update    LINK    existing series #75 “Dragonriders of Pern”" in card

    def test_interstitial_position(self):
        p = _create_proposal()
        p["book"]["create"]["series_position"] = None
        card = render_card(_resolution(), _epub_meta(), p, 1, 1)
        assert " position  none (interstitial)" in card

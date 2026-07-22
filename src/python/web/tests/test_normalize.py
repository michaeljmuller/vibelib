from web.ingest.normalize import (
    loose_title,
    norm_language,
    norm_person,
    norm_title,
    parse_pub_date,
    sort_name,
    sort_title,
    split_authors,
    title_is_junk,
)


class TestNormPerson:
    def test_initials_variants_collide(self):
        assert (
            norm_person("J. R. R. Tolkien")
            == norm_person("J.R.R. Tolkien")
            == norm_person("JRR Tolkien")
            == "jrr tolkien"
        )

    def test_middle_initial_is_preserved(self):
        # Iain Banks vs Iain M. Banks is an LLM decision, not a tier-1 collision
        assert norm_person("Iain Banks") != norm_person("Iain M. Banks")

    def test_case_and_punctuation(self):
        assert norm_person("stephen KING") == norm_person("Stephen King")
        assert norm_person("Ursula K. Le Guin") == norm_person("Ursula K Le Guin")

    def test_diacritics(self):
        assert norm_person("Gabriel García Márquez") == norm_person(
            "Gabriel Garcia Marquez"
        )

    def test_apostrophes(self):
        assert norm_person("Patrick O'Brian") == norm_person("Patrick OBrian")

    def test_comma_inverted_form_collides(self):
        assert norm_person("King, Stephen") == norm_person("Stephen King")
        assert norm_person("Le Guin, Ursula K.") == norm_person("Ursula K. Le Guin")

    def test_comma_suffix_is_not_inversion(self):
        assert norm_person("Martin Luther King, Jr.") == norm_person(
            "Martin Luther King Jr."
        )


class TestTitles:
    def test_leading_article_dropped(self):
        assert norm_title("The Martian") == norm_title("Martian")

    def test_punctuation_insensitive(self):
        assert norm_title("Hitchhiker's Guide") == norm_title("Hitchhikers Guide")

    def test_loose_strips_parenthetical(self):
        assert loose_title("We Are Legion (We Are Bob)") == loose_title("We Are Legion")

    def test_loose_strips_subtitle(self):
        assert loose_title("Dune: The Graphic Novel") == loose_title("Dune")

    def test_strict_keeps_subtitle(self):
        assert norm_title("Dune: The Graphic Novel") != norm_title("Dune")


class TestSortForms:
    def test_sort_name(self):
        assert sort_name("Stephen King") == "King, Stephen"
        assert sort_name("Ursula K. Le Guin") == "Guin, Ursula K. Le"
        assert sort_name("Cher") == "Cher"

    def test_sort_name_suffix(self):
        assert sort_name("Martin Luther King Jr.") == "King, Martin Luther Jr."

    def test_sort_title(self):
        assert sort_title("The Martian") == "Martian, The"
        assert sort_title("A Memory Called Empire") == "Memory Called Empire, A"
        assert sort_title("Dune") == "Dune"


class TestSplitAuthors:
    def test_ampersand(self):
        assert split_authors("Daniel Abraham & Ty Franck") == [
            "Daniel Abraham",
            "Ty Franck",
        ]

    def test_and(self):
        assert split_authors("Terry Pratchett and Neil Gaiman") == [
            "Terry Pratchett",
            "Neil Gaiman",
        ]

    def test_semicolon_and_slash(self):
        assert split_authors("A One; B Two/C Three") == ["A One", "B Two", "C Three"]

    def test_comma_not_split(self):
        assert split_authors("King, Stephen") == ["King, Stephen"]

    def test_none(self):
        assert split_authors(None) == []


class TestParsePubDate:
    def test_full_date(self):
        assert parse_pub_date("2011-05-03") == "2011-05-03"

    def test_year_only_becomes_jan_first(self):
        assert parse_pub_date("1959") == "1959-01-01"

    def test_year_month(self):
        assert parse_pub_date("1965-08") == "1965-08-01"

    def test_garbage(self):
        assert parse_pub_date("unknown") is None
        assert parse_pub_date("2011-13-45") is None
        assert parse_pub_date(None) is None
        assert parse_pub_date("") is None


class TestNormLanguage:
    def test_simple(self):
        assert norm_language("en") == "en"
        assert norm_language("EN") == "en"

    def test_region(self):
        assert norm_language("pt-pt") == "pt-PT"
        assert norm_language("pt_BR") == "pt-BR"

    def test_garbage(self):
        assert norm_language("english language") is None
        assert norm_language(None) is None
        assert norm_language("") is None


class TestTitleIsJunk:
    def test_normal_title_is_fine(self):
        assert not title_is_junk("Project Hail Mary")

    def test_empty(self):
        assert title_is_junk(None)
        assert title_is_junk("   ")

    def test_filename(self):
        assert title_is_junk("project_hail_mary.m4b")
        assert title_is_junk("the_martian")

    def test_single_word_real_title_ok(self):
        assert not title_is_junk("Dune")

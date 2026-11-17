"""Free discovery: a source that needs no key, and typed categories that
survive a typo.

OpenStreetMap was the default and maps nine trades, so anyone searching for
a university got zero results and no hint why. These cover the free web
provider that replaced it, and the spelling help in front of it.
"""

import pytest

from harvey.collectors.discover import (
    DEFAULT_PROVIDER, PROVIDERS, DiscoveryQuery, normalise_category,
)


# -- the provider costs nothing and asks for nothing --


def test_the_default_provider_needs_no_key():
    assert DEFAULT_PROVIDER == "free_web"
    p = PROVIDERS[DEFAULT_PROVIDER]
    assert p.needs_key is False
    assert p.configured({}) is True
    assert p.env_keys == ()


def test_it_estimates_zero_because_it_is_zero():
    p = PROVIDERS["free_web"]
    queries = [DiscoveryQuery(term="universities", location="Chennai")
               for _ in range(50)]
    assert p.estimate(queries) == 0.0


def test_it_does_not_advertise_a_signup():
    """Nothing to sign up for, so no link that implies otherwise."""
    assert PROVIDERS["free_web"].signup_url == ""


@pytest.mark.asyncio
async def test_results_become_businesses_with_a_rank():
    p = PROVIDERS["free_web"]
    hits = [("Anna University", "https://www.annauniv.edu/courses"),
            ("IIT Madras", "https://www.iitm.ac.in/"),
            ("Anna University again", "https://annauniv.edu/other")]
    out = p._to_businesses(
        hits, DiscoveryQuery(term="universities", location="Chennai", limit=10))
    assert [b.domain for b in out] == ["annauniv.edu", "iitm.ac.in"], "deduped by domain"
    assert out[0].rank == 1 and out[1].rank == 2
    assert out[0].location == "Chennai"
    assert all(b.has_website for b in out)


@pytest.mark.asyncio
async def test_the_limit_is_respected():
    p = PROVIDERS["free_web"]
    hits = [(f"U{i}", f"https://u{i}.edu/") for i in range(20)]
    out = p._to_businesses(
        hits, DiscoveryQuery(term="universities", location="Chennai", limit=4))
    assert len(out) == 4


# -- a typo should not cost you the search --


@pytest.mark.parametrize("typed,expected", [
    ("universithy", "universities"),
    ("university", "universities"),
    ("colleges", "colleges and universities"),
    ("comunity colleges", "community colleges"),
    ("career centre", "career centers"),
    ("placement cell", "career services"),
    ("b school", "business schools"),
    ("technical institute", "technical institutions"),
])
def test_categories_are_normalised(typed, expected):
    assert normalise_category(typed) == expected


def test_a_department_searches_for_its_institution():
    """A department is not a thing you find on a map; you find the
    institution and read its people off its own site."""
    assert normalise_category("HR Departments") == "universities"
    assert normalise_category("marketing department") == "universities"


def test_an_unrecognised_term_is_left_alone():
    """Rewriting a deliberate term into a guess is worse than passing it
    through: the person may know something the alias table does not."""
    assert normalise_category("maritime academies") == "maritime academies"
    assert normalise_category("") == ""


def test_whitespace_and_case_do_not_matter():
    assert normalise_category("  UNIVERSITHY  ") == "universities"

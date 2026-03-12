"""Tests for the TripAdvisor hotel enrichment pipeline.

Covers:
- Hotel name normalization
- Destination normalization and aliases
- TripAdvisor URL/ID extraction
- Confidence scoring and status assignment
- Ambiguous match detection
- Real-world hotel name examples from our dataset
"""

import pytest

from app.enrichment.normalize import (
    normalize_hotel_name,
    normalize_hotel_name_aggressive,
    normalize_destination,
    get_country_for_destination,
    strip_accents,
)
from app.enrichment.tripadvisor_matcher import (
    MatchResult,
    SeedHotel,
    SourceHotel,
    TripAdvisorMatcher,
    extract_tripadvisor_id,
    extract_destination_from_url,
)


# ---------------------------------------------------------------------------
# Normalization tests
# ---------------------------------------------------------------------------

class TestStripAccents:
    def test_accented_chars(self):
        assert strip_accents("México") == "Mexico"
        assert strip_accents("Samaná") == "Samana"
        assert strip_accents("Cancún") == "Cancun"

    def test_plain_text_unchanged(self):
        assert strip_accents("Punta Cana") == "Punta Cana"


class TestNormalizeHotelName:
    def test_ampersand_normalization(self):
        a = normalize_hotel_name("Hard Rock Hotel & Casino Punta Cana")
        b = normalize_hotel_name("Hard Rock Hotel and Casino Punta Cana")
        assert a == b

    def test_html_entity_cleanup(self):
        result = normalize_hotel_name("Resort &amp; Spa")
        assert "amp" not in result
        assert "and" in result

    def test_accents_stripped(self):
        result = normalize_hotel_name("Villa La Valencia Beach Resort & Spa Los Cabos, México")
        assert "mexico" not in result  # trailing destination stripped by comma rule
        assert "villa la valencia" in result

    def test_brand_suffix_stripped(self):
        result = normalize_hotel_name("Viva V Samana by Wyndham, A Trademark All Inclusive")
        assert "wyndham" not in result
        assert "trademark" not in result
        assert "viva v samana" in result or "viva" in result

    def test_case_insensitive(self):
        a = normalize_hotel_name("Secrets The Vine Cancun")
        b = normalize_hotel_name("SECRETS THE VINE CANCUN")
        assert a == b

    def test_whitespace_collapsed(self):
        result = normalize_hotel_name("Grand   Park  Royal   Puerto Vallarta")
        assert "  " not in result

    def test_trailing_destination_stripped(self):
        result = normalize_hotel_name("Iberostar Selection Bavaro Suites, Punta Cana")
        assert "punta cana" not in result


class TestNormalizeHotelNameAggressive:
    def test_strips_resort_hotel_spa(self):
        result = normalize_hotel_name_aggressive("Secrets The Vine Resort & Spa")
        assert "resort" not in result
        assert "spa" not in result
        assert "secrets" in result
        assert "vine" in result

    def test_strips_common_words(self):
        result = normalize_hotel_name_aggressive("The Grand Hotel Suites Beach Club")
        assert "the" not in result.split()
        assert "hotel" not in result.split()


class TestNormalizeDestination:
    def test_alias_resolution(self):
        assert normalize_destination("Riviera Nayarit") == "nuevo vallarta"
        assert normalize_destination("Bayahibe") == "la romana"
        assert normalize_destination("Cabo San Lucas") == "los cabos"

    def test_case_insensitive(self):
        assert normalize_destination("Playa Del Carmen") == "playa del carmen"
        assert normalize_destination("PLAYA DEL CARMEN") == "playa del carmen"
        assert normalize_destination("playa del carmen") == "playa del carmen"

    def test_accents(self):
        assert normalize_destination("Samaná") == "samana"

    def test_unknown_destination(self):
        result = normalize_destination("Unknown Place")
        assert result == "unknown place"


class TestGetCountry:
    def test_known_destinations(self):
        assert get_country_for_destination("punta cana") == "Dominican Republic"
        assert get_country_for_destination("cancun") == "Mexico"
        assert get_country_for_destination("montego bay") == "Jamaica"
        assert get_country_for_destination("varadero") == "Cuba"

    def test_unknown(self):
        assert get_country_for_destination("unknown") is None


# ---------------------------------------------------------------------------
# TripAdvisor URL/ID extraction
# ---------------------------------------------------------------------------

class TestExtractTripAdvisorId:
    def test_standard_url(self):
        url = "https://www.tripadvisor.com/Hotel_Review-g147293-d152337-Reviews-Hard_Rock_Hotel_Casino_Punta_Cana-Bavaro_Punta_Cana.html"
        assert extract_tripadvisor_id(url) == 152337

    def test_different_id(self):
        url = "https://www.tripadvisor.com/Hotel_Review-g150807-d500123-Reviews-Some_Hotel.html"
        assert extract_tripadvisor_id(url) == 500123

    def test_no_id(self):
        assert extract_tripadvisor_id("https://tripadvisor.com/some-page") is None

    def test_empty_url(self):
        assert extract_tripadvisor_id("") is None
        assert extract_tripadvisor_id(None) is None


class TestExtractDestinationFromUrl:
    def test_cancun(self):
        url = "https://www.tripadvisor.com/Hotel_Review-g150807-d27671171-Reviews-AVA_Resort_Cancun-Cancun_Yucatan_Peninsula.html"
        assert "Cancun" in extract_destination_from_url(url)

    def test_punta_cana_with_province(self):
        url = "https://www.tripadvisor.com/Hotel_Review-g3176298-d25269002-Reviews-Adults_Only_Club-Bavaro_Punta_Cana_La_Altagracia_Prov.html"
        result = extract_destination_from_url(url)
        assert "Punta Cana" in result or "Bavaro" in result

    def test_pagination_url(self):
        url = "https://www.tripadvisor.com/Hotel_Review-g147343-d149215-Reviews-or20-Windjammer_Landing-Castries_Castries_Quarter_St_Lucia.html"
        result = extract_destination_from_url(url)
        assert "Lucia" in result

    def test_query_params_stripped(self):
        url = "https://www.tripadvisor.com/Hotel_Review-g147417-d151524-Reviews-Warwick_Resort-Paradise_Island_Bahamas.html?"
        result = extract_destination_from_url(url)
        assert "Bahamas" in result

    def test_empty_url(self):
        assert extract_destination_from_url("") == ""
        assert extract_destination_from_url(None) == ""


# ---------------------------------------------------------------------------
# Matching engine tests
# ---------------------------------------------------------------------------

def _make_matcher(seeds: list[dict]) -> TripAdvisorMatcher:
    """Helper to create a matcher from simple dicts."""
    seed_hotels = []
    for s in seeds:
        seed_hotels.append(SeedHotel(
            tripadvisor_url=s.get("url", "https://tripadvisor.com/Hotel_Review-g1-d1-Reviews-Test.html"),
            tripadvisor_name=s["name"],
            destination=s.get("destination", ""),
        ))
    return TripAdvisorMatcher(seed_hotels)


class TestExactMatching:
    def test_exact_name_and_destination(self):
        matcher = _make_matcher([
            {"name": "Hard Rock Hotel and Casino Punta Cana", "destination": "Punta Cana"},
        ])
        hotel = SourceHotel(
            hotel_name="Hard Rock Hotel & Casino Punta Cana",
            destination_str="Punta Cana",
        )
        result = matcher.match(hotel)
        assert result.review_status == "matched"
        assert result.match_confidence == 1.0
        assert result.match_method == "exact_name_plus_destination"

    def test_exact_name_different_destination(self):
        matcher = _make_matcher([
            {"name": "Grand Park Royal", "destination": "Cancun"},
        ])
        hotel = SourceHotel(hotel_name="Grand Park Royal", destination_str="Puerto Vallarta")
        result = matcher.match(hotel)
        assert result.match_confidence > 0.90  # exact name, different dest
        assert result.match_method == "exact_name_only"


class TestFuzzyMatching:
    def test_minor_name_difference(self):
        matcher = _make_matcher([
            {"name": "Iberostar Selection Bavaro Suites", "destination": "Punta Cana"},
        ])
        hotel = SourceHotel(
            hotel_name="Iberostar Selection Bavaro Suite",  # missing trailing 's'
            destination_str="Punta Cana",
        )
        result = matcher.match(hotel)
        assert result.review_status == "matched"
        assert result.match_confidence >= 0.90

    def test_brand_suffix_difference(self):
        matcher = _make_matcher([
            {"name": "Viva V Samana", "destination": "Samana"},
        ])
        hotel = SourceHotel(
            hotel_name="Viva V Samana by Wyndham, A Trademark All Inclusive",
            destination_str="Samana",
        )
        result = matcher.match(hotel)
        # Brand suffix stripped → should match well
        assert result.match_confidence >= 0.80
        assert result.review_status in ("matched", "needs_manual_review")


class TestAmbiguousMatching:
    def test_very_short_name_goes_to_not_found_with_candidate(self):
        """A bare generic name with no destination is too vague — should be not_found
        but still surface the best candidate in notes."""
        matcher = _make_matcher([
            {"name": "Hard Rock Hotel Vallarta", "destination": "Puerto Vallarta",
             "url": "https://tripadvisor.com/Hotel_Review-g1-d100-Reviews.html"},
            {"name": "Hard Rock Hotel Riviera Maya", "destination": "Riviera Maya",
             "url": "https://tripadvisor.com/Hotel_Review-g1-d200-Reviews.html"},
        ])
        hotel = SourceHotel(hotel_name="Hard Rock Hotel", destination_str="")
        result = matcher.match(hotel)
        assert result.review_status == "not_found"
        assert "Best candidate" in (result.notes or "")

    def test_close_scores_flagged_ambiguous(self):
        """Two seeds with very similar names to the source — runner-up gap < 0.05."""
        matcher = _make_matcher([
            {"name": "Iberostar Bavaro Suites", "destination": "Punta Cana",
             "url": "https://tripadvisor.com/Hotel_Review-g1-d100-Reviews.html"},
            {"name": "Iberostar Bavaro Resort", "destination": "Punta Cana",
             "url": "https://tripadvisor.com/Hotel_Review-g1-d200-Reviews.html"},
        ])
        hotel = SourceHotel(hotel_name="Iberostar Bavaro", destination_str="Punta Cana")
        result = matcher.match(hotel)
        # Both candidates are close — should flag for review
        assert result.review_status in ("ambiguous", "needs_manual_review")


class TestNotFound:
    def test_no_seed_match(self):
        matcher = _make_matcher([
            {"name": "Completely Different Hotel", "destination": "London"},
        ])
        hotel = SourceHotel(hotel_name="Secrets The Vine Cancun", destination_str="Cancun")
        result = matcher.match(hotel)
        assert result.review_status in ("not_found", "needs_manual_review")


# ---------------------------------------------------------------------------
# Real-world hotel examples from our dataset
# ---------------------------------------------------------------------------

class TestRealWorldHotels:
    """Test with actual hotel names from our deals database."""

    @pytest.fixture
    def matcher(self):
        return _make_matcher([
            {"name": "Grand Park Royal Puerto Vallarta", "destination": "Puerto Vallarta",
             "url": "https://www.tripadvisor.com/Hotel_Review-g150793-d155000-Reviews.html"},
            {"name": "Hard Rock Hotel & Casino Punta Cana", "destination": "Punta Cana",
             "url": "https://www.tripadvisor.com/Hotel_Review-g147293-d152337-Reviews.html"},
            {"name": "Hard Rock Hotel Vallarta", "destination": "Puerto Vallarta",
             "url": "https://www.tripadvisor.com/Hotel_Review-g150793-d1050000-Reviews.html"},
            {"name": "Iberostar Selection Bavaro Suites", "destination": "Punta Cana",
             "url": "https://www.tripadvisor.com/Hotel_Review-g147293-d250000-Reviews.html"},
            {"name": "Secrets The Vine Cancun", "destination": "Cancun",
             "url": "https://www.tripadvisor.com/Hotel_Review-g150807-d1750000-Reviews.html"},
            {"name": "Villa La Valencia Beach Resort & Spa Los Cabos", "destination": "Los Cabos",
             "url": "https://www.tripadvisor.com/Hotel_Review-g152515-d2000000-Reviews.html"},
        ])

    def test_grand_park_royal(self, matcher):
        hotel = SourceHotel(hotel_name="Grand Park Royal Puerto Vallarta", destination_str="Puerto Vallarta")
        result = matcher.match(hotel)
        assert result.review_status == "matched"
        assert result.match_confidence >= 0.95

    def test_hard_rock_punta_cana(self, matcher):
        hotel = SourceHotel(hotel_name="Hard Rock Hotel and Casino Punta Cana", destination_str="Punta Cana")
        result = matcher.match(hotel)
        assert result.review_status == "matched"
        assert result.match_confidence >= 0.95

    def test_secrets_vine(self, matcher):
        hotel = SourceHotel(hotel_name="Secrets The Vine Cancun", destination_str="Cancun")
        result = matcher.match(hotel)
        assert result.review_status == "matched"
        assert result.match_confidence >= 0.95

    def test_villa_la_valencia_with_accented_source(self, matcher):
        hotel = SourceHotel(
            hotel_name="Villa La Valencia Beach Resort & Spa Los Cabos, México",
            destination_str="Los Cabos",
        )
        result = matcher.match(hotel)
        assert result.review_status == "matched"
        assert result.match_confidence >= 0.85

    def test_hard_rock_vallarta_distinct_from_punta_cana(self, matcher):
        hotel = SourceHotel(hotel_name="Hard Rock Hotel Vallarta", destination_str="Puerto Vallarta")
        result = matcher.match(hotel)
        assert result.review_status == "matched"
        assert "vallarta" in (result.tripadvisor_matched_name or "").lower()

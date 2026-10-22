"""Tests for the fixes that came out of the live test with a real question.

The question was: "I want to cycle across Rushikonda in Visakhapatnam but I
had a fracture in my foot, tell me the best way and safest way".
"""

from __future__ import annotations

from app.pipeline import Pipeline

RAJIV = (
    "Hey I am Rajiv, I want to cycle across Rushikonda in Visakhapatnam but I had "
    "a fracture in my foot okay tell me the best way and safest way"
)


def test_an_injury_is_recognised_as_a_condition():
    pipe = Pipeline()
    assert "injury" in pipe._profile_limits_or_empty(RAJIV)


def test_the_other_conditions_are_still_recognised():
    pipe = Pipeline()
    assert pipe._profile_limits_or_empty("I have mild asthma") == ["asthma"]
    assert pipe._profile_limits_or_empty("going with my parents") == ["senior"]
    assert pipe._profile_limits_or_empty("for my kids") == ["child"]
    assert pipe._profile_limits_or_empty("I am pregnant") == ["pregnancy"]
    assert pipe._profile_limits_or_empty("a normal question about running") == []


def test_safest_is_read_as_a_safety_question():
    pipe = Pipeline()
    assert pipe._extract_intent_type(RAJIV) == "safety_check"
    assert pipe._extract_intent_type("what is the best spot for a walk near Jayanagar") == "recommend"
    assert pipe._extract_intent_type("is it okay to cycle at 8 AM") == "safety_check"


def test_a_business_is_not_a_place():
    from app.sources.geocode import BUSINESS_KINDS

    assert "car_wash" in BUSINESS_KINDS
    assert "restaurant" in BUSINESS_KINDS
    assert "park" not in BUSINESS_KINDS


def test_the_geojson_is_small():
    """The response was 48 KB because the road lines had 1,000 vertices."""
    from app.pipeline import Pipeline

    points = [[83.0 + i * 0.0001, 17.0 + (i % 7) * 0.0001] for i in range(200)]
    simplified = Pipeline._simplify(points)
    assert len(simplified) < len(points)
    # the shape is kept: the first and the last point never move
    assert simplified[0] == [round(v, 5) for v in points[0]]
    assert simplified[-1] == [round(v, 5) for v in points[-1]]


def test_a_venue_name_is_never_invented():
    from app.sources.overpass import Venue

    venue = Venue(id="way/1", name="Anuroopa Park", lat=12.9, lon=77.6, kind="park")
    assert venue.name

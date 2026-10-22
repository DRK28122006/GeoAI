"""The prompt contract.

The empty <think></think> block we pre-seed in the assistant turn is what
keeps the JSON grammar active. ggml issue 20345 reports that grammar
enforcement is silently not applied when thinking is enabled, and Qwen3
thinks by default, so the block is not cosmetic. scripts/check_grammar.py
checks the real behaviour with the model loaded; these tests check the
contract that makes it work.
"""

from __future__ import annotations

from app.slm import CHOICE_SCHEMA, INTENT_SCHEMA, SLM


def test_the_prompt_carries_the_empty_think_block():
    prompt = SLM._chatml("system", "user")
    assert prompt.endswith("<|im_start|>assistant\n<think>\n\n</think>\n\n")
    assert "<|im_start|>system\n" in prompt
    assert "<|im_start|>user\n" in prompt


def test_the_prompt_ends_with_the_generation_prompt():
    """A trailing assistant header is what makes the model answer directly."""
    prompt = SLM._chatml("system", "user")
    assert prompt.rstrip().endswith("</think>")


def test_the_intent_schema_only_asks_for_fields_we_use():
    """Every field has to earn its place: the schema costs grammar time."""
    required = set(INTENT_SCHEMA["required"])
    assert required == {
        "activity",
        "location",
        "day",
        "part_of_day",
        "profile",
    }
    # The hour and the out of scope flag are decided in code, not by the
    # model, so they are not in the schema.
    assert "hour" not in required
    assert "out_of_scope" not in required


def test_the_choice_schema_asks_for_the_headline_and_nothing_more():
    assert set(CHOICE_SCHEMA["required"]) == {"slot_id", "headline"}


def test_the_schemas_are_json_serialisable():
    import json

    json.dumps(INTENT_SCHEMA)
    json.dumps(CHOICE_SCHEMA)

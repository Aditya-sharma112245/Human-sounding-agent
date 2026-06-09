"""
tests/test_vibe_check.py
─────────────────────────
Verifies that the Vibe Check node correctly identifies and would trigger
a rewrite for corporate/AI-sounding text.

These are pure unit tests — no LLM calls needed for flag detection.
"""

import pytest
from app.agent.nodes import _needs_rewrite


@pytest.mark.parametrize("text,expected", [
    # Should fail (needs rewrite)
    ("Certainly! I would be happy to help you with that.", True),
    ("As an AI language model, I cannot provide personal advice.", True),
    ("Thank you for reaching out. I understand your concern.", True),
    ("Absolutely! Great question. Here is a detailed explanation of machine learning:", True),
    ("I apologize for any confusion this may have caused.", True),
    # Emoji check
    ("sounds good! 🙌 let me know if you need anything 😊", True),
    # Too long
    ("a" * 350, True),

    # Should pass (already human-sounding)
    ("yeah makes sense", False),
    ("what company is it for?", False),
    ("interviews can be rough", False),
    ("how did it go?", False),
    ("got it", False),
    ("that's actually interesting. what made you pick that role?", False),
])
def test_needs_rewrite(text: str, expected: bool):
    result = _needs_rewrite(text)
    assert result == expected, (
        f"Expected _needs_rewrite({text!r}) == {expected}, got {result}"
    )

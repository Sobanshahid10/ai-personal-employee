"""Tests for html_email.py."""

from __future__ import annotations

import unittest

from html_email import convert_text_to_html_blocks, render_html_email


class HTMLEmailTests(unittest.TestCase):
    def test_convert_text_to_html_blocks_paragraphs(self) -> None:
        text = "Hello world.\n\nThank you for your inquiry."
        result = convert_text_to_html_blocks(text)
        self.assertIn("<p", result)
        self.assertIn("Hello world.", result)
        self.assertIn("Thank you for your inquiry.", result)

    def test_convert_text_to_html_blocks_lists(self) -> None:
        text = "Here are the points:\n* First item\n* Second item"
        result = convert_text_to_html_blocks(text)
        self.assertIn("<ul", result)
        self.assertIn("First item</li>", result)
        self.assertIn("Second item</li>", result)

    def test_convert_text_to_html_blocks_formatting_and_escaping(self) -> None:
        text = "Check **bold** & <script>alert(1)</script> and https://example.com"
        result = convert_text_to_html_blocks(text)
        self.assertIn("<strong>bold</strong>", result)
        self.assertIn("&amp; &lt;script&gt;", result)
        self.assertIn('href="https://example.com"', result)

    def test_render_html_email(self) -> None:
        html_out = render_html_email("Test content", subject="Test Subject")
        self.assertIn("<!DOCTYPE html>", html_out)
        self.assertIn("Test Subject", html_out)
        self.assertIn("Test content", html_out)
        self.assertIn("ChiefMind AI", html_out)


if __name__ == "__main__":
    unittest.main()

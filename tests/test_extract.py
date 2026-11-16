"""Tests for twowebs.extract (Layer 3)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from twowebs import extract  # noqa: E402


def lines_of(body, ct="text/html"):
    if isinstance(body, str):
        body = body.encode("utf-8")
    return extract.text_lines(body, ct)[0]


def norm_text(body, ct="text/html"):
    return extract.norm(" ".join(lines_of(body, ct)))


class NormTests(unittest.TestCase):
    def test_case_and_punctuation(self):
        self.assertEqual(extract.norm("Hello,  World!"), "hello world")

    def test_decimal_point_and_currency_kept(self):
        self.assertEqual(extract.norm("Costs $49.99."), "costs $49.99")
        self.assertEqual(extract.norm("Version 2.0 is 20% faster."),
                         "version 2.0 is 20% faster")

    def test_quotes_and_dashes_become_spaces(self):
        self.assertEqual(extract.norm("don’t “stop” — now"),
                         extract.norm("don't \"stop\" - now"))

    def test_underscores_and_markdown_marks(self):
        self.assertEqual(extract.norm("snake_case **bold**"), "snake case bold")


class SentenceTests(unittest.TestCase):
    def test_min_words_and_dedupe(self):
        lines = ["Short one. This sentence has enough words in it.",
                 "This sentence has enough words in it!"]
        out = extract.sentences(lines, 6)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0], "this sentence has enough words in it")
        self.assertEqual(out[0][1], "This sentence has enough words in it.")

    def test_order_preserved(self):
        out = extract.sentences(["Alpha beta gamma delta epsilon zeta.",
                                 "One two three four five six seven."], 6)
        self.assertEqual([o[0].split()[0] for o in out], ["alpha", "one"])


class FactTests(unittest.TestCase):
    def test_price_percent_date_number(self):
        f = extract.facts("Now $1,299.00, 20% off until March 3, 2025; 12,000 buyers.")
        self.assertIn("price:$1299.00", f)
        self.assertIn("percent:20%", f)
        self.assertIn("date:march3,2025".replace(",", ""), f)
        self.assertIn("number:12000", f)

    def test_price_with_code(self):
        self.assertIn("price:49.99usd", extract.facts("Only 49.99 USD today"))

    def test_iso_date(self):
        self.assertIn("date:2025-01-31", extract.facts("Due 2025-01-31."))

    def test_entities(self):
        self.assertIn("entity:acme widgets inc", extract.facts("Made by Acme Widgets Inc in Ohio"))

    def test_plain_text_has_no_hard_facts(self):
        f = extract.facts("this is plain prose with no figures at all")
        self.assertFalse([x for x in f if x.split(":")[0] in extract.HARD_TYPES])

    def test_hard_types(self):
        self.assertEqual(set(extract.HARD_TYPES), {"price", "percent", "date", "number"})


class TextLinesTests(unittest.TestCase):
    def test_html_skips_script_style_svg(self):
        html = ("<html><head><style>p{color:red}</style><script>var x=1;</script></head>"
                "<body><svg><text>icon</text></svg><p>Visible text.</p></body></html>")
        self.assertEqual(lines_of(html), ["Visible text."])

    def test_html_block_elements_split_lines(self):
        self.assertEqual(lines_of("<html><body><p>One</p><p>Two<br>Three</p></body></html>"),
                         ["One", "Two", "Three"])

    def test_html_entities_decoded(self):
        self.assertEqual(lines_of("<html><body><p>A &amp; B&nbsp;C</p></body></html>"),
                         ["A & B C"])

    def test_meta_description_included(self):
        html = '<html><head><meta name="description" content="About us."></head><body><p>x</p></body></html>'
        self.assertIn("About us.", lines_of(html))

    def test_links_returned(self):
        _l, links, is_html = extract.text_lines(
            b'<html><body><a href="/a/b">x</a></body></html>', "text/html")
        self.assertEqual(links, ["/a/b"])
        self.assertTrue(is_html)

    def test_markdown_strips_syntax(self):
        md = ("# Title\n\nSome **bold** and [a link](http://x.test/a) here.\n\n"
              "- item one\n- item two\n\n1. first\n2. second\n\n> quoted\n")
        self.assertEqual(lines_of(md, "text/markdown"),
                         ["Title", "Some bold and a link here.", "item one", "item two",
                          "first", "second", "quoted"])

    def test_markdown_table(self):
        out = lines_of("| Plan | Price |\n|------|-------|\n| Pro | $9 |\n", "text/markdown")
        self.assertEqual(norm_text("| Plan | Price |\n| Pro | $9 |\n", "text/markdown"),
                         "plan price pro $9")
        self.assertIn("Plan Price", out)

    def test_content_type_charset(self):
        body = "café au lait".encode("latin-1")
        self.assertEqual(extract.decode(body, "text/html; charset=ISO-8859-1"), "café au lait")

    def test_charset_from_meta(self):
        body = b'<meta charset="windows-1252"><p>caf\xe9</p>'
        self.assertIn("café", extract.decode(body, "text/html"))

    def test_unknown_charset_falls_back(self):
        self.assertEqual(extract.decode(b"abc", "text/html; charset=nonsense-9"), "abc")

    def test_html_detected_without_content_type(self):
        self.assertTrue(extract.looks_like_html("<!DOCTYPE html><p>x</p>", "text/plain"))
        self.assertFalse(extract.looks_like_html("# Heading", "text/markdown"))


class PickInnerLinkTests(unittest.TestCase):
    BASE = "https://example.com/"

    def test_first_long_same_host_link(self):
        links = ["#top", "/", "/about", "https://other.test/a-very-long-path-here",
                 "/blog/a-fairly-long-article-title?x=1#frag", "/blog/second-article-title"]
        self.assertEqual(extract.pick_inner_link(links, self.BASE),
                         "https://example.com/blog/a-fairly-long-article-title?x=1")

    def test_skips_assets_and_account_pages(self):
        links = ["/downloads/annual-report-2024.pdf", "/user/login-page-here-now",
                 "/shop/checkout-and-payment", "mailto:a@b.test", "/products/widget-overview"]
        self.assertEqual(extract.pick_inner_link(links, self.BASE),
                         "https://example.com/products/widget-overview")

    def test_none_when_nothing_qualifies(self):
        self.assertIsNone(extract.pick_inner_link(["/a", "/b/c"], self.BASE))

    # Regression: hostnames are case-insensitive.
    def test_host_comparison_is_case_insensitive(self):
        got = extract.pick_inner_link(["https://EXAMPLE.com/blog/a-fairly-long-title"], self.BASE)
        self.assertEqual(got, "https://EXAMPLE.com/blog/a-fairly-long-title")


class MarkdownVsHtmlTests(unittest.TestCase):
    """The Markdown path must give text that norm-matches the HTML path."""

    def _same(self, html, md):
        h = norm_text("<html><body>" + html + "</body></html>")
        m = norm_text(md, "text/markdown")
        self.assertIn(m, h, "markdown text not found in html text")
        self.assertIn(h, m, "html text not found in markdown text")

    def test_heading_bold_link(self):
        self._same("<h1>Pricing</h1><p>Plans <b>start</b> at <a href='/p'>$49.99</a> a month.</p>",
                   "# Pricing\n\nPlans **start** at [$49.99](/p) a month.\n")

    def test_lists(self):
        self._same("<ul><li>First thing here.</li><li>Second thing here.</li></ul>"
                   "<ol><li>Step one.</li><li>Step two.</li></ol>",
                   "- First thing here.\n- Second thing here.\n\n1. Step one.\n2. Step two.\n")

    def test_table(self):
        self._same("<table><tr><th>Plan</th><th>Price</th></tr><tr><td>Pro</td><td>$9</td></tr></table>",
                   "| Plan | Price |\n|---|---|\n| Pro | $9 |\n")

    def test_inline_image_alt_is_kept_in_markdown_only(self):
        # Documented asymmetry, not a defect: Markdown keeps the alt text, the
        # HTML path ignores <img>.
        self.assertIn("a red widget", norm_text("![A red widget](/w.png)", "text/markdown"))
        self.assertNotIn("red widget", norm_text("<html><body><img src='/w.png' alt='A red widget'></body></html>"))

    # Regression: reference-style links and their "[1]: url" definitions.
    def test_reference_style_links(self):
        self._same("<p>See <a href='/pricing'>our pricing page</a> for details.</p>",
                   "See [our pricing page][1] for details.\n\n[1]: /pricing\n")

    # Regression: HTML entities written in Markdown source are decoded.
    def test_entities_in_markdown(self):
        self._same("<p>Questions &amp; answers about widgets today.</p>",
                   "Questions &amp; answers about widgets today.\n")


if __name__ == "__main__":
    unittest.main()


class EscapeAndOverlapTests(unittest.TestCase):
    def test_markdown_escapes_do_not_hide_facts(self):
        md = extract._markdown_to_text("Rated 4\.5 of 5, up 12\.3% to \$99")
        got = set(x for x in extract.facts(md) if not x.startswith("entity"))
        self.assertEqual(got, {"number:4.5", "percent:12.3%", "price:$99"})

    def test_price_and_percent_are_not_also_numbers(self):
        got = set(x for x in extract.facts("costs $150,000 or 12.3% of 3306") if not x.startswith("entity"))
        self.assertEqual(got, {"price:$150000", "percent:12.3%", "number:3306"})

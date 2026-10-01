"""Offline tests for the GrazeCart diagnostic harness."""
import json
from pathlib import Path

from app.fetcher.grazecart_diagnostics import (
    detected_actions,
    extract_cards_from_html,
    extract_option_rows_from_html,
    is_safe_option_text,
    is_unsafe_cart_text,
    redact_secrets,
    run_diagnostics,
)


CARD_HTML = """
<main>
  <article class="productListing product-card">
    <h3>Large Eggs</h3>
    <p class="subtitle">Case options available</p>
    <span>$7.00</span>
    <a href="/store/eggs/pastured-eggs">Details</a>
    <button type="button">Select Option</button>
  </article>
  <article class="productListing product-card">
    <h3>Butter</h3>
    <span>$11.00</span>
    <button type="button" wire:click="addToCart(123)">Add to Order</button>
  </article>
</main>
"""


OPTION_HTML = """
<div class="option-panel">
  <div class="option-row selected">
    <span>1 Dozen Eggs</span>
    <span>$7.00</span>
    <span>Selected</span>
  </div>
  <div class="modifier-option">
    <span>Case of 8 Dozen Eggs</span>
    <span>$52.00</span>
    <span>Save $4.00</span>
  </div>
  <tr class="variant-row"><td>Case of 16 Dozen Eggs</td><td>$96.00</td><td>Save 14%</td></tr>
</div>
"""


def test_card_detection_reports_buttons_links_actions_and_snippets():
    cards = extract_cards_from_html(CARD_HTML)

    assert len(cards) == 2
    assert cards[0]["title"] == "Large Eggs"
    assert cards[0]["price_text"] == "$7.00"
    assert "Select Option" in cards[0]["button_texts"]
    assert cards[0]["detail_links"] == ["/store/eggs/pastured-eggs"]
    assert "select_option" in cards[0]["detected_actions"]
    assert "direct_add_to_order" in cards[1]["detected_actions"]
    assert "wire:click" in cards[1]["safe_html_snippet"]


def test_select_option_detection_is_safe_and_excludes_add_to_order():
    assert is_safe_option_text("Select Option")
    assert is_safe_option_text("Choose Options")
    assert not is_safe_option_text("Add to Order")
    assert is_unsafe_cart_text("Add to Cart")
    assert detected_actions(["Choose Options", "Add to Order"], []) == [
        "choose_options",
        "direct_add_to_order",
    ]


def test_option_row_parsing_extracts_price_savings_selected_and_text():
    rows = extract_option_rows_from_html(OPTION_HTML)

    assert len(rows) == 3
    assert rows[0]["label"].startswith("1 Dozen Eggs")
    assert rows[0]["price"] == "$7.00"
    assert rows[0]["selected_default"] is True
    assert rows[1]["savings"] == "Case of 8 Dozen Eggs $52.00 Save $4.00"
    assert rows[2]["savings"] == "Case of 16 Dozen Eggs $96.00 Save 14%"


def test_secret_redaction_removes_tokens_snapshots_and_passwords():
    html = (
        '<button wire:snapshot="secret-json" data-csrf-token="abc" '
        'password="hunter2">Select Option</button>'
    )

    redacted = redact_secrets(html)

    assert "secret-json" not in redacted
    assert "abc" not in redacted
    assert "hunter2" not in redacted
    assert "[redacted]" in redacted


def test_diagnostic_report_shape_with_mocked_browser(tmp_path, monkeypatch):
    class FakeLocator:
        def __init__(self, items=None, text="", html=""):
            self.items = items or []
            self.text = text
            self.html = html

        def filter(self, **_kwargs):
            return self

        def count(self):
            return len(self.items)

        def nth(self, index):
            return self.items[index]

        def first(self):
            return self.items[0]

        def evaluate(self, _script):
            return self.html

        def inner_text(self, timeout=None):
            return self.text

        def bounding_box(self):
            return {"x": 1, "y": 2, "width": 3, "height": 4}

        def locator(self, _selector):
            if "Select Option" in self.text:
                return FakeLocator([FakeButton()])
            return FakeLocator([])

    class FakeButton:
        def count(self):
            return 1

        def first(self):
            return self

        def click(self, timeout=None):
            return None

    class FakePage:
        url = "https://example.test/eggs"

        def __init__(self):
            self.after = False
            self.card = FakeLocator(
                text="Large Eggs $7.00 Select Option",
                html=CARD_HTML.split("</article>")[0] + "</article>",
            )

        def goto(self, url, wait_until=None):
            self.url = url

        def wait_for_timeout(self, _ms):
            self.after = True

        def content(self):
            return CARD_HTML + (OPTION_HTML if self.after else "")

        def screenshot(self, path, full_page=True):
            Path(path).write_bytes(b"png")

        def locator(self, selector):
            if selector == "body":
                return FakeLocator(text="Large Eggs $7.00 Select Option")
            return FakeLocator([self.card])

        def close(self):
            return None

    class FakeContext:
        def new_page(self):
            return FakePage()

    class FakeBrowser:
        contexts = [FakeContext()]

        def close(self):
            return None

    class FakeChromium:
        def connect_over_cdp(self, *_args, **_kwargs):
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeSyncPlaywright:
        def __enter__(self):
            return FakePlaywright()

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr("app.fetcher.grazecart_diagnostics._port_is_listening", lambda port: True)
    monkeypatch.setattr("app.fetcher.grazecart_diagnostics.vendor_debug_port", lambda vendor_id: 9222)
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakeSyncPlaywright())

    report = run_diagnostics(
        vendor_id="vendor_b",
        url="https://example.test/eggs",
        output_dir=tmp_path,
    )
    panels = (tmp_path / "option_panels.json").read_text(encoding="utf-8")

    assert report["cards_found"] == 1
    assert report["select_option_cards"] == 1
    assert report["option_rows_extracted"] == 3
    assert "hidden_dom_rows_before_click" in panels
    assert "Case of 8 Dozen Eggs" in panels
    assert (tmp_path / "report.json").exists()
    assert (tmp_path / "cards.json").exists()
    assert (tmp_path / "option_panels.json").exists()
    assert (tmp_path / "safe_snippets.json").exists()


def test_diagnostic_prefers_card_scoped_hidden_rows_after_click(tmp_path, monkeypatch):
    card_one_html = """
    <article class="product-card"><h3>Large Brown Eggs</h3><button>Select Option</button>
      <table><tr><td>Large Brown Eggs</td><td>$9.45</td></tr>
      <tr><td>8 Dozen Bundle Large Brown Eggs</td><td>$71.60</td><td>$4.00</td></tr></table>
    </article>
    """
    card_two_html = """
    <article class="product-card"><h3>Dozen DUCK EGGS</h3><button>Select Option</button>
      <table><tr><td>Dozen DUCK EGGS</td><td>$11.25</td></tr></table>
    </article>
    """

    class FakeLocator:
        def __init__(self, items=None, text="", html=""):
            self.items = items or []
            self.text = text
            self.html = html

        def filter(self, **_kwargs):
            return self

        def count(self):
            return len(self.items)

        def nth(self, index):
            return self.items[index]

        @property
        def first(self):
            return self.items[0]

        def evaluate(self, _script):
            return self.html

        def inner_text(self, timeout=None):
            return self.text

        def bounding_box(self):
            return None

        def locator(self, _selector):
            return FakeLocator([FakeButton()])

    class FakeButton:
        def count(self):
            return 1

        @property
        def first(self):
            return self

        def click(self, timeout=None):
            return None

    class FakePage:
        url = "https://example.test/eggs"

        def __init__(self):
            self.cards = [
                FakeLocator(text="Large Brown Eggs $9.45 Select Option", html=card_one_html),
                FakeLocator(text="Dozen DUCK EGGS $11.25 Select Option", html=card_two_html),
            ]

        def goto(self, url, wait_until=None):
            self.url = url

        def wait_for_timeout(self, _ms):
            return None

        def content(self):
            return card_one_html + card_two_html

        def screenshot(self, path, full_page=True):
            Path(path).write_bytes(b"png")

        def locator(self, selector):
            if selector == "body":
                return FakeLocator(text="Large Brown Eggs Dozen DUCK EGGS Select Option")
            return FakeLocator(self.cards)

        def close(self):
            return None

    class FakeContext:
        def new_page(self):
            return FakePage()

    class FakeBrowser:
        contexts = [FakeContext()]

        def close(self):
            return None

    class FakeChromium:
        def connect_over_cdp(self, *_args, **_kwargs):
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeSyncPlaywright:
        def __enter__(self):
            return FakePlaywright()

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr("app.fetcher.grazecart_diagnostics._port_is_listening", lambda port: True)
    monkeypatch.setattr("app.fetcher.grazecart_diagnostics.vendor_debug_port", lambda vendor_id: 9222)
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakeSyncPlaywright())

    report = run_diagnostics(
        vendor_id="vendor_b",
        url="https://example.test/eggs",
        output_dir=tmp_path,
    )
    panels = json.loads((tmp_path / "option_panels.json").read_text(encoding="utf-8"))

    assert report["option_rows_extracted"] == 3
    assert [panel["option_rows_count"] for panel in panels] == [2, 1]
    assert "Dozen DUCK EGGS" not in " ".join(panels[0]["row_texts"])

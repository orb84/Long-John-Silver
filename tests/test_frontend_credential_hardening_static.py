"""Static checks for browser password-manager false-positive hardening."""

from pathlib import Path


def test_dom_hardens_dynamic_and_template_form_controls() -> None:
    dom_js = Path('src/web/static/js/components/dom.js').read_text()

    assert 'hardenFormControl' in dom_js
    assert "new-password" in dom_js
    assert "data-ljs-noncredential" in dom_js
    assert 'DOMContentLoaded' in dom_js
    assert 'MutationObserver' in dom_js
    assert 'startCredentialHardeningObserver' in dom_js

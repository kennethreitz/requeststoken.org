"""Helpers for the multi-page site: tests check content across all pages."""
import pathlib

PAGES = ["/", "/community", "/faq", "/safety", "/updates", "/library", "/projects", "/about"]
TEMPLATES = ["base", "home", "community", "faq", "safety", "updates", "library", "projects", "about"]


def site_source() -> str:
    return "\n".join(pathlib.Path(f"templates/{name}.html").read_text() for name in TEMPLATES)


def site_html(client) -> str:
    out = []
    for path in PAGES:
        response = client.get(path)
        assert response.status_code == 200, path
        out.append(response.text)
    return "\n".join(out)

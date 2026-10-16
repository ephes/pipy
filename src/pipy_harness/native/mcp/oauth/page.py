"""The browser page a sign-in ends on.

``render_sign_in_page`` is the ``render_page`` of the callback server during
an MCP sign-in. The pages are Pi's (``oauthSuccessHtml`` and
``oauthErrorHtml``) without Pi's logo; every text is escaped.
"""

from __future__ import annotations

import html
from typing import Final

from pipy_harness.native.mcp.oauth.callback import OAuthCallbackPage

SIGNED_IN_MESSAGE: Final = "Signed in to the MCP server. You may now close this page."

_STYLE: Final = """\
    :root {
      --text: #fafafa;
      --text-dim: #a1a1aa;
      --page-bg: #09090b;
      --font-sans: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans", sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol", "Noto Color Emoji";
      --font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
    }
    * { box-sizing: border-box; }
    html { color-scheme: dark; }
    body {
      margin: 0;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
      background: var(--page-bg);
      color: var(--text);
      font-family: var(--font-sans);
      text-align: center;
    }
    main {
      width: 100%;
      max-width: 560px;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
    }
    h1 {
      margin: 0 0 10px;
      font-size: 28px;
      line-height: 1.15;
      font-weight: 650;
      color: var(--text);
    }
    p {
      margin: 0;
      line-height: 1.7;
      color: var(--text-dim);
      font-size: 15px;
    }
    .details {
      margin-top: 16px;
      font-family: var(--font-mono);
      font-size: 13px;
      color: var(--text-dim);
      white-space: pre-wrap;
      word-break: break-word;
    }"""


def _render(heading: str, message: str, details: str | None = None) -> str:
    title = html.escape(heading)
    block = f'<div class="details">{html.escape(details)}</div>' if details else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
{_STYLE}
  </style>
</head>
<body>
  <main>
    <h1>{title}</h1>
    <p>{html.escape(message)}</p>
    {block}
  </main>
</body>
</html>"""


def oauth_success_html(message: str) -> str:
    return _render("Authentication successful", message)


def oauth_error_html(message: str, details: str | None = None) -> str:
    return _render("Authentication failed", message, details)


def render_sign_in_page(page: OAuthCallbackPage) -> str:
    """The HTML page for the outcome of an MCP sign-in."""

    if page.ok:
        return oauth_success_html(SIGNED_IN_MESSAGE)
    return oauth_error_html(page.message, page.details)

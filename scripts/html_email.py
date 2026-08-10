"""ChiefMind HTML email rendering module.

Converts email draft text into beautiful, responsive HTML emails with modern CSS styling,
cards, clear hierarchy, dark/light mode support, and executive signature branding.
"""

from __future__ import annotations

import html
import re


def _format_inline_text(text: str) -> str:
    """Escape HTML and process basic inline formatting (bold, italic, links)."""
    escaped = html.escape(text)
    # **bold** or __bold__
    escaped = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"__(.*?)__", r"<strong>\1</strong>", escaped)
    # *italic* or _italic_
    escaped = re.sub(r"\*(.*?)\*", r"<em>\1</em>", escaped)
    escaped = re.sub(r"(?<!\w)_(.*?)_(?!\w)", r"<em>\1</em>", escaped)
    # URLs -> clickable links
    url_pattern = re.compile(
        r"(https?://[^\s<]+)"
    )
    escaped = url_pattern.sub(
        r'<a href="\1" style="color: #4f46e5; text-decoration: underline;" target="_blank">\1</a>',
        escaped,
    )
    return escaped


def convert_text_to_html_blocks(text: str) -> str:
    """Convert text with paragraphs and list items into clean HTML block elements."""
    lines = text.strip().splitlines()
    html_blocks: list[str] = []
    in_list = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_list:
                html_blocks.append("</ul>")
                in_list = False
            continue

        # Bullet points: *, -, •
        if re.match(r"^[\*\-•]\s+", stripped):
            if not in_list:
                html_blocks.append('<ul style="margin: 12px 0; padding-left: 24px; color: #374151;">')
                in_list = True
            content = re.sub(r"^[\*\-•]\s+", "", stripped)
            formatted = _format_inline_text(content)
            html_blocks.append(f'<li style="margin-bottom: 6px; line-height: 1.6;">{formatted}</li>')
        # Numbered list: 1., 2., etc.
        elif re.match(r"^\d+\.\s+", stripped):
            if in_list:
                html_blocks.append("</ul>")
                in_list = False
            content = re.sub(r"^\d+\.\s+", "", stripped)
            formatted = _format_inline_text(content)
            html_blocks.append(f'<p style="margin: 8px 0; line-height: 1.6; color: #374151;">{formatted}</p>')
        # Blockquote: > text
        elif stripped.startswith(">"):
            if in_list:
                html_blocks.append("</ul>")
                in_list = False
            content = stripped.lstrip(">").strip()
            formatted = _format_inline_text(content)
            html_blocks.append(
                f'<blockquote style="margin: 16px 0; padding: 12px 16px; border-left: 4px solid #6366f1; background-color: #f3f4f6; color: #4b5563; font-style: italic; border-radius: 0 6px 6px 0;">{formatted}</blockquote>'
            )
        else:
            if in_list:
                html_blocks.append("</ul>")
                in_list = False
            formatted = _format_inline_text(stripped)
            html_blocks.append(f'<p style="margin: 14px 0; line-height: 1.65; color: #374151; font-size: 15px;">{formatted}</p>')

    if in_list:
        html_blocks.append("</ul>")

    return "\n".join(html_blocks)


def render_html_email(
    body_text: str,
    subject: str = "",
    sender_name: str = "ChiefMind Executive Assistant",
) -> str:
    """Render full responsive HTML email wrapper for body text."""
    formatted_body = convert_text_to_html_blocks(body_text)
    display_subject = html.escape(subject) if subject else "Message from ChiefMind"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{display_subject}</title>
  <style>
    body {{
      margin: 0;
      padding: 0;
      background-color: #f8fafc;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
      -webkit-font-smoothing: antialiased;
    }}
    .email-wrapper {{
      max-width: 620px;
      margin: 20px auto;
      background: #ffffff;
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.05);
      border: 1px solid #e2e8f0;
    }}
    .email-header {{
      background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%);
      padding: 24px 32px;
      color: #ffffff;
    }}
    .email-header h1 {{
      margin: 0;
      font-size: 20px;
      font-weight: 600;
      letter-spacing: -0.2px;
    }}
    .email-header p {{
      margin: 4px 0 0 0;
      font-size: 12px;
      opacity: 0.85;
      text-transform: uppercase;
      letter-spacing: 0.8px;
    }}
    .email-body {{
      padding: 32px;
      color: #334155;
    }}
    .email-footer {{
      background-color: #f1f5f9;
      padding: 20px 32px;
      border-top: 1px solid #e2e8f0;
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 12px;
      color: #64748b;
    }}
    .brand-badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-weight: 600;
      color: #4f46e5;
    }}
    @media (prefers-color-scheme: dark) {{
      body {{ background-color: #0f172a; }}
      .email-wrapper {{ background: #1e293b; border-color: #334155; color: #f1f5f9; }}
      .email-body p {{ color: #cbd5e1 !important; }}
      .email-footer {{ background-color: #0f172a; border-color: #334155; color: #94a3b8; }}
    }}
  </style>
</head>
<body>
  <div class="email-wrapper">
    <div class="email-header">
      <h1>{display_subject}</h1>
      <p>{html.escape(sender_name)}</p>
    </div>
    <div class="email-body">
      {formatted_body}
    </div>
    <div class="email-footer">
      <div>
        <span class="brand-badge">⚡ ChiefMind AI</span> — Autonomous Assistant
      </div>
      <div>Verified & Signed</div>
    </div>
  </div>
</body>
</html>"""

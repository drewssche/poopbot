"""Compile the personal daily widget: python -m scripts.build_private_widget.

Only regeneration needs Node. The server ships the compiled template so polling
and sending a question do not require a frontend toolchain.
"""
import re
import subprocess

from app.services.group_daily_widget import ASSETS, source_template


def compact(match):
    tag, source = match.groups()
    loader = "css" if tag == "style" else "js"
    result = subprocess.run(
        ["npx", "--yes", "esbuild@0.28.2", "--minify", "--charset=utf8", f"--loader={loader}", "--target=es2020"],
        input=source, capture_output=True, text=True, check=True,
    )
    return f"<{tag}>{result.stdout.strip()}</{tag}>"


if __name__ == "__main__":
    markup = re.sub(r"<(style|script)>(.*?)</\1>", compact, source_template(True), flags=re.DOTALL)
    (ASSETS / "daily-private.min.html").write_text(markup.strip() + "\n")

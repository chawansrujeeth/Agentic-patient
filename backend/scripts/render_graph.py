from __future__ import annotations

from pathlib import Path
import base64
import sys

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from graph import build_graph  # noqa: E402


def main() -> None:
    out_path = Path("graph_pipeline_2.png")
    graph = build_graph(None).get_graph()

    mermaid = graph.draw_mermaid(with_styles=False)
    encoded = base64.b64encode(mermaid.encode("utf-8")).decode("ascii")
    url = f"https://mermaid.ink/img/{encoded}?type=png&bgColor=!white"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    out_path.write_bytes(resp.content)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()

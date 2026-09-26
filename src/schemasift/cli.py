from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .config import build_selector, load_config
from .models import SchemaSelectionRequest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="schemasift")
    parser.add_argument("--config", default="schemasift.yaml")
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    select = subparsers.add_parser("select")
    select.add_argument("request", help="JSON request file, or - for stdin")
    providers = subparsers.add_parser("providers")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    if args.command == "serve":
        try:
            import uvicorn
        except ImportError as exc:
            parser.error("server dependencies are required: pip install 'schemasift[server]'")
        from .api import create_app
        uvicorn.run(create_app(config), host=args.host or config.server.host,
                    port=args.port or config.server.port)
        return 0
    selector = build_selector(config)
    if args.command == "providers":
        print(json.dumps({name: {"adapter": provider.adapter, "model": provider.model}
                          for name, provider in selector.providers.items()}, indent=2))
        return 0
    text = __import__("sys").stdin.read() if args.request == "-" else Path(args.request).read_text()
    request = SchemaSelectionRequest.model_validate_json(text)
    result = asyncio.run(selector.select(request))
    print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


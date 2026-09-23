"""CLI entry point for the local vision service.

Startup order matters: home-service must run first because it owns the shared
ingress token this service reads. Models are loaded once, and a missing model is
reported through the API rather than crashing the process, so the Unity panel can
show a specific reason instead of a connection error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from config import VisionConfig
from pipeline import VisionRuntime
from server import create_server

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "vision.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Local face and pose vision service (binds to 127.0.0.1 only)"
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--print-config", action="store_true")
    args = parser.parse_args(argv)

    if not args.config.exists():
        print(f"configuration not found: {args.config}", file=sys.stderr)
        return 2
    try:
        config = VisionConfig.load(args.config)
    except (ValueError, OSError) as exc:
        print(f"invalid configuration: {exc}", file=sys.stderr)
        return 2

    if args.print_config:
        print(json.dumps(config.public_view(), ensure_ascii=False, indent=2))
        return 0

    runtime = VisionRuntime(config)
    runtime.load_models()
    runtime.start_worker()

    try:
        server = create_server(runtime, config.host, config.port)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"vision-service listening on http://{config.host}:{config.port} (loopback only)")
    status = runtime.status()
    if not status["model_ready"]:
        print(
            "warning: models are not ready "
            f"({status['model_error']}); the API reports the reason instead of failing to start"
        )
    if not runtime.sync.available():
        print(
            "warning: runtime/vision/home-service.token is missing; "
            "start home-service first or person locations cannot be published"
        )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.server_close()
        # One bounded offline notification, then release the camera even if
        # home-service is unreachable.
        runtime.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Backward-compatibility CLI redirecting to evals.bird.cli."""

from evals.bird.cli import main

if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(help="Personal AI factory CLI.", no_args_is_help=True)


@app.callback()
def _callback() -> None:
    pass


@app.command()
def version() -> None:
    """Print the factory version."""
    typer.echo("ai_factory 0.1.0 (phase 1 — single-shot executor)")


@app.command()
def run_ticket(
    ticket_file: Path = typer.Argument(..., help="Path to the ticket markdown file."),
    repo: str = typer.Option(..., "--repo", help="Repo key from manifest.yaml."),
    manifest: Path | None = typer.Option(None, "--manifest", help="Path to manifest.yaml (default: ./manifest.yaml)."),
) -> None:
    """Run a ticket through the executor pipeline and open a PR."""
    from .runner import run_ticket as _run

    try:
        _run(ticket_file, repo, manifest)
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


if __name__ == "__main__":
    app()

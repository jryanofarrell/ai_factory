import typer

app = typer.Typer(help="Personal AI factory CLI.", no_args_is_help=True)


@app.callback()
def _callback() -> None:
    pass


@app.command()
def version() -> None:
    """Print the factory version."""
    typer.echo("ai_factory 0.0.0 (phase 0 — scaffolding)")


if __name__ == "__main__":
    app()

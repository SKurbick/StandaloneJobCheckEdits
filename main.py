"""Executable entrypoint for the refactored standalone job."""

if __package__:
    from .job import run
else:
    from job import run


if __name__ == "__main__":
    run()

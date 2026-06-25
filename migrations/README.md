# Alembic migrations

Database migrations for the ClauseOps web platform (`app/` package).

The environment (`env.py`) loads the async database URL from
`app.config.get_settings().database_url` and uses `app.data.database.Base.metadata`
(with `app.data.models` imported) as the autogenerate target, so all ORM tables,
indexes, foreign-key cascade rules, enum types, and check constraints are tracked.

## Common commands

Run from the project root with the project virtualenv active:

```bash
alembic upgrade head      # apply all migrations
alembic downgrade base    # roll everything back
alembic revision --autogenerate -m "describe change"
alembic current           # show the current revision
```

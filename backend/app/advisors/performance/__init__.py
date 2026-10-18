"""Performance rules, one module per engine (``common`` applies to every engine).

Importing the package registers every rule with :mod:`app.advisors.engine`."""

from app.advisors.performance import common, db2, db2zos, mysql, oracle, postgresql, sqlserver  # noqa: F401

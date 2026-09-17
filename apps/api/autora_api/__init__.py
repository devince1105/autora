"""HTTP layer. Routing, request/response shapes, auth and error format only.

Business logic lives in ``autora``: routers call repositories for reads and company/runtime
operations for writes, and never touch the database any other way.
"""

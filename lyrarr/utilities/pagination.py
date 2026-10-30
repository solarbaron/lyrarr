"""Shared paging parameters for list endpoints."""

from flask import request

# Large enough for "show everything" style views, small enough that one
# request can't make the server serialize the whole library.
MAX_PAGE_SIZE = 500


def page_params(default_size=25):
    """(page, page_size) from the query string, clamped to sane bounds."""
    page = request.args.get('page', 1, type=int) or 1
    page_size = request.args.get('pageSize', default_size, type=int) or default_size
    return max(page, 1), min(max(page_size, 1), MAX_PAGE_SIZE)

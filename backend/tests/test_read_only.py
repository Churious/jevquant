from app.main import app


def test_http_surface_has_no_write_route():
    assert all(
        not (getattr(route, "methods", set()) & {"POST", "PUT", "PATCH", "DELETE"})
        for route in app.routes
    )

from __future__ import annotations

from flask.testing import FlaskClient


def test_home_renders(client: FlaskClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"GG Afrikan Eleganz" in resp.data
    assert resp.headers["Content-Type"].startswith("text/html")


def test_unknown_route_is_404(client: FlaskClient) -> None:
    assert client.get("/no-such-page/").status_code == 404

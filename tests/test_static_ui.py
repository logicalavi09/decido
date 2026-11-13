from __future__ import annotations


def test_ui_shell_is_served_with_assets(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Decido" in response.text
    assert "/js/app.js" in response.text
    assert "/styles.css" in response.text

    assets = {
        "/styles.css": "text/css",
        "/favicon.svg": "image/svg+xml",
        "/js/app.js": "text/javascript",
        "/js/api.js": "text/javascript",
        "/js/views.js": "text/javascript",
        "/js/icons.js": "text/javascript",
        "/assets/fonts/manrope-400.woff2": "font/woff2",
    }
    for path, content_type in assets.items():
        asset = client.get(path)
        assert asset.status_code == 200, path
        assert asset.headers["content-type"].startswith(content_type), path


def test_ui_mount_does_not_shadow_api_routes(client):
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "Swagger UI" in docs.text

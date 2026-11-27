from __future__ import annotations

from typing import Any

import pytest

from weimi_manager.update_runtime import fetch_signed_channel


class _Response:
    def __init__(
        self,
        *,
        url: str,
        payload: Any,
    ) -> None:
        self.url = url
        self._payload = payload

    def raise_for_status(self) -> None:
        return

    def json(self) -> Any:
        return self._payload


def test_fetch_channel_resolves_named_asset_from_github_release_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_url = (
        "https://api.github.com/repos/Zpf365/langlang-updates/"
        "releases/tags/v1.9.02"
    )
    asset_url = (
        "https://github.com/Zpf365/langlang-updates/releases/download/"
        "v1.9.02/hot_update_channel.json"
    )
    signed = {
        "schema_version": 1,
        "kind": "channel",
        "channel": "stable",
        "sequence": 1,
        "product_version": "1.9.02",
        "patches": [],
        "signature": {"algorithm": "ed25519"},
    }
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_get(url: str, **kwargs: Any) -> _Response:
        calls.append((url, kwargs))
        if url == api_url:
            return _Response(
                url=api_url,
                payload={
                    "assets": [
                        {
                            "name": "LangLang-1.9.02-win64.zip",
                            "browser_download_url": (
                                "https://github.com/Zpf365/langlang-updates/"
                                "releases/download/v1.9.02/"
                                "LangLang-1.9.02-win64.zip"
                            ),
                        },
                        {
                            "name": "hot_update_channel.json",
                            "browser_download_url": asset_url,
                        },
                    ]
                },
            )
        assert url == asset_url
        return _Response(url=asset_url, payload=signed)

    monkeypatch.setattr(
        "weimi_manager.update_runtime.requests.get",
        fake_get,
    )

    assert fetch_signed_channel(api_url) == signed
    assert [url for url, _kwargs in calls] == [api_url, asset_url]
    assert calls[0][1]["headers"]["Accept"].startswith(
        "application/vnd.github"
    )


def test_fetch_channel_rejects_missing_github_channel_asset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_url = (
        "https://api.github.com/repos/Zpf365/langlang-updates/"
        "releases/tags/v1.9.02"
    )

    def fake_get(url: str, **_kwargs: Any) -> _Response:
        return _Response(
            url=url,
            payload={"assets": []},
        )

    monkeypatch.setattr(
        "weimi_manager.update_runtime.requests.get",
        fake_get,
    )

    with pytest.raises(ValueError, match="hot_update_channel.json"):
        fetch_signed_channel(api_url)


@pytest.mark.parametrize(
    "source",
    [
        "http://api.github.com/repos/Zpf365/langlang-updates/releases/latest",
        "file:///C:/updates/hot_update_channel.json",
        "https://user:secret@example.invalid/hot_update_channel.json",
    ],
)
def test_fetch_channel_rejects_non_public_https_source(source: str) -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        fetch_signed_channel(source)


def test_fetch_channel_rejects_redirect_to_plain_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "https://example.invalid/hot_update_channel.json"

    def fake_get(url: str, **_kwargs: Any) -> _Response:
        return _Response(
            url="http://example.invalid/hot_update_channel.json",
            payload={"kind": "channel"},
        )

    monkeypatch.setattr(
        "weimi_manager.update_runtime.requests.get",
        fake_get,
    )

    with pytest.raises(ValueError, match="HTTPS"):
        fetch_signed_channel(source)

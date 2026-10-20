"""Получение refresh token для Google Ads API.

Работает и с desktop-, и с web-клиентом: тип определяется по файлу client_secret*.json.
- desktop (`installed`): Google разрешает любой локальный порт, берём свободный;
- web: redirect URI должен совпасть буква в букву, поэтому берём первый локальный URI,
  зарегистрированный у клиента, и поднимаем сервер ровно на нём.

Запуск:
    python3 scripts/google_ads_oauth.py ~/Downloads/client_secret_*.json
"""
from __future__ import annotations

import json
import sys
from urllib.parse import urlparse

SCOPES = ["https://www.googleapis.com/auth/adwords"]


def server_options(config):
    """(kwargs для run_local_server, тип клиента) или (None, тип) если web без локального URI."""
    if "installed" in config:
        return {"host": "localhost", "port": 0, "redirect_uri_trailing_slash": True}, "desktop"
    for uri in (config.get("web") or {}).get("redirect_uris") or []:
        parsed = urlparse(uri)
        if parsed.hostname in ("localhost", "127.0.0.1"):
            return ({"host": parsed.hostname, "port": parsed.port or 80,
                     "redirect_uri_trailing_slash": uri.endswith("/")}, "web")
    return None, "web"


def main(path):
    config = json.load(open(path, encoding="utf-8"))
    options, kind = server_options(config)
    print(f"файл: {path}")
    print(f"тип клиента: {kind}")
    if options is None:
        print("\nУ этого web-клиента не зарегистрирован ни один локальный redirect URI.")
        print("APIs & Services → Credentials → твой OAuth-клиент → Authorized redirect URIs → Add URI:")
        print("    http://localhost:8080/")
        print("Save, подожди минуту и запусти команду снова.")
        return 1
    slash = "/" if options["redirect_uri_trailing_slash"] else ""
    print(f"redirect: http://{options['host']}:{options['port']}{slash}")

    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(path, scopes=SCOPES)
    credentials = flow.run_local_server(prompt="consent", access_type="offline", **options)
    if not credentials.refresh_token:
        print("\nGoogle не вернул refresh token. Отзови доступ на myaccount.google.com/permissions и повтори.")
        return 1
    client = flow.client_config
    print("\n--- скопировать целиком ---")
    print("GOOGLE_ADS_CLIENT_ID=" + client["client_id"])
    print("GOOGLE_ADS_CLIENT_SECRET=" + client["client_secret"])
    print("GOOGLE_ADS_REFRESH_TOKEN=" + credentials.refresh_token)
    print("GOOGLE_ADS_CUSTOMER_ID=          # Customer ID из ads.google.com, без дефисов")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))

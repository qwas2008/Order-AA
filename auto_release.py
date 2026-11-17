#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Автоматический выпуск обновления (релиза) для Заказ АА+:
1. Проверяет наличие токена GitHub.
2. Фиксирует изменения в git и пушит в main.
3. Определяет следующий номер релиза/сборки (например, 35).
4. Собирает оптимизированный Android APK (arm64-v8a).
5. Создает новый GitHub Release в репозитории qwas2008/Order-AA.
6. Загружает собранный APK как Zakaz_AA.apk к релизу.
"""

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime

REPO = "qwas2008/Order-AA"
TOKEN_FILE = ".gist_token"
FLET_BIN = "/Library/Frameworks/Python.framework/Versions/3.13/bin/flet"
APK_PATH = "build/apk/app-release.apk"
ASSET_NAME = "Zakaz_AA.apk"


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def get_token(script_dir):
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and token.strip():
        return token.strip()

    token_path = os.path.join(script_dir, TOKEN_FILE)
    if os.path.exists(token_path):
        try:
            with open(token_path, "r", encoding="utf-8") as f:
                saved = f.read().strip()
            if saved:
                return saved
        except Exception:
            pass
    return None


def get_headers(token, content_type="application/json"):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "AmwayApp-AutoRelease",
        "Content-Type": content_type,
    }


def find_next_tag(token, repo):
    url = f"https://api.github.com/repos/{repo}/releases?per_page=50"
    req = urllib.request.Request(url, headers=get_headers(token))
    tags = []
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for rel in data:
                tag = str(rel.get("tag_name", "")).strip()
                if tag.isdigit():
                    tags.append(int(tag))
    except Exception as e:
        log(f"⚠️ Не удалось получить список релизов: {e}")

    # Также проверим git tags через API
    url_tags = f"https://api.github.com/repos/{repo}/tags?per_page=50"
    req_tags = urllib.request.Request(url_tags, headers=get_headers(token))
    try:
        with urllib.request.urlopen(req_tags, timeout=10) as resp:
            data_tags = json.loads(resp.read().decode("utf-8"))
            for t in data_tags:
                tag = str(t.get("name", "")).strip()
                if tag.isdigit():
                    tags.append(int(tag))
    except Exception as e:
        log(f"⚠️ Не удалось получить список тегов: {e}")

    if tags:
        return max(tags) + 1
    return 35


def git_commit_and_push(token, repo, tag_num):
    log("Проверяем состояние Git...")
    try:
        status_proc = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True
        )
        if status_proc.stdout.strip():
            log("Найдены изменённые файлы. Добавляем и создаём коммит...")
            subprocess.run(["git", "add", "-A"], check=True)
            commit_msg = f"Релиз v{tag_num} ({datetime.now().strftime('%d.%m.%Y %H:%M')})"
            subprocess.run(["git", "commit", "-m", commit_msg], check=True)
        else:
            log("Рабочее дерево Git чистое.")

        push_url = f"https://x-access-token:{token}@github.com/{repo}.git"
        log("Отправляем коммиты в origin main...")
        subprocess.run(
            ["git", "push", push_url, "main"],
            capture_output=True,
            text=True,
            check=True
        )
        log("✅ Изменения успешно отправлены в GitHub.")
    except subprocess.CalledProcessError as err:
        log(f"⚠️ Ошибка при работе с Git (продолжаем сборку): {err}")


def build_apk(tag_num):
    log(f"🔨 Запуск сборки Android APK (сборка №{tag_num})...")
    build_cmd = [
        FLET_BIN,
        "build",
        "apk",
        "--arch", "arm64-v8a",
        "--exclude", ".git", "storage", "build",
        "--project", "zakaz_aa_plus",
        "--product", "Заказ АА+",
        "--org", "com.anton",
        "--build-number", str(tag_num),
    ]

    res = subprocess.run(build_cmd)
    if res.returncode != 0:
        log(f"❌ Ошибка сборки Flet APK (код {res.returncode})")
        sys.exit(res.returncode)

    if not os.path.exists(APK_PATH):
        log(f"❌ Файл APK не найден по пути: {APK_PATH}")
        sys.exit(1)

    size_mb = os.path.getsize(APK_PATH) / (1024 * 1024)
    log(f"✅ APK успешно собран: {APK_PATH} ({size_mb:.1f} MB)")


def create_release(token, repo, tag_num):
    log(f"🚀 Создание GitHub Release с тегом '{tag_num}'...")
    url = f"https://api.github.com/repos/{repo}/releases"
    payload = {
        "tag_name": str(tag_num),
        "target_commitish": "main",
        "name": f"Заказ АА+",
        "body": f"Автоматический релиз Заказ АА+ (сборка {tag_num})\n\n"
                f"- Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
                f"- Архитектура: arm64-v8a\n"
                f"- APK: Zakaz_AA.apk",
        "draft": False,
        "prerelease": False
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers=get_headers(token)
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            log(f"✅ Релиз успешно создан: {data.get('html_url')}")
            return data
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8")
        log(f"❌ Не удалось создать релиз (HTTP {e.code}): {err_msg}")
        sys.exit(1)


def upload_asset(token, upload_url_template, apk_file):
    # upload_url_template: https://uploads.github.com/repos/owner/repo/releases/id/assets{?name,label}
    upload_url = re.sub(r"\{.*?\}", "", upload_url_template)
    upload_url += f"?name={ASSET_NAME}"

    size_bytes = os.path.getsize(apk_file)
    log(f"📦 Загружаем ассет {ASSET_NAME} ({size_bytes / (1024 * 1024):.1f} MB) в GitHub Release...")

    headers = get_headers(token, content_type="application/vnd.android.package-archive")
    headers["Content-Length"] = str(size_bytes)

    with open(apk_file, "rb") as f:
        apk_data = f.read()

    req = urllib.request.Request(
        upload_url,
        data=apk_data,
        method="POST",
        headers=headers
    )

    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            asset_info = json.loads(resp.read().decode("utf-8"))
            log(f"✅ Ассет успешно загружен!")
            download_url = asset_info.get("browser_download_url")
            log(f"🔗 Ссылка на скачивание: {download_url}")
            return download_url
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8")
        log(f"❌ Ошибка загрузки ассета (HTTP {e.code}): {err_msg}")
        sys.exit(1)


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    token = get_token(script_dir)
    if not token:
        log("❌ GitHub токен не найден в переменных окружения или в .gist_token")
        sys.exit(1)

    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        tag_num = int(sys.argv[1])
        log(f"Используем указанный номер релиза: {tag_num}")
    else:
        tag_num = find_next_tag(token, REPO)
        log(f"Определён следующий номер релиза: {tag_num}")

    # 1. Фиксируем и пушим код
    git_commit_and_push(token, REPO, tag_num)

    # 2. Собираем APK
    build_apk(tag_num)

    # 3. Создаём релиз
    release_data = create_release(token, REPO, tag_num)

    # 4. Загружаем Zakaz_AA.apk
    upload_url = release_data.get("upload_url")
    download_url = upload_asset(token, upload_url, APK_PATH)

    log("=" * 50)
    log(f"🎉 Выпуск обновления завершён успешно!")
    log(f"📱 Тег / Сборка: {tag_num}")
    log(f"🌐 Релиз: {release_data.get('html_url')}")
    log(f"📥 Скачать APK: {download_url}")
    log(f"⭐ Постоянная ссылка (latest): https://github.com/{REPO}/releases/latest/download/{ASSET_NAME}")
    log("=" * 50)


if __name__ == "__main__":
    main()

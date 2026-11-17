#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Автоматический выпуск обновления (релиза) для Заказ АА+:
1. Проверяет наличие токена GitHub.
2. Автоматически увеличивает APP_VERSION в main.py на 1.
3. Фиксирует изменения в git и отправляет коммит в ветку main.
4. Собирает оптимизированный Android APK (arm64-v8a, --build-number).
5. Создает новый GitHub Release в репозитории qwas2008/Order-AA.
6. Загружает собранный APK как Zakaz_AA.apk к релизу.
7. Обновляет файл app_info.json в GitHub Gist (5503a740bfc7c96e8f76f7e7a3dae417)
   со свежей версией, прямой ссылкой apk_url и описанием changes.
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
GIST_ID = "5503a740bfc7c96e8f76f7e7a3dae417"
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


def get_current_app_version(script_dir):
    main_path = os.path.join(script_dir, "main.py")
    if os.path.exists(main_path):
        with open(main_path, "r", encoding="utf-8") as f:
            match = re.search(r"APP_VERSION\s*=\s*(\d+)", f.read())
            if match:
                return int(match.group(1))
    return 36


def update_main_app_version(new_version, script_dir):
    main_path = os.path.join(script_dir, "main.py")
    with open(main_path, "r", encoding="utf-8") as f:
        content = f.read()

    new_content, count = re.subn(
        r"APP_VERSION\s*=\s*\d+",
        f"APP_VERSION = {new_version}",
        content
    )
    if count == 0:
        # Если переменной не было, добавим её в начало
        new_content = f"APP_VERSION = {new_version}\n" + content

    with open(main_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    log(f"✅ В main.py обновлена переменная: APP_VERSION = {new_version}")


def find_next_tag(token, repo):
    tags = []
    # 1. Проверяем релизы
    url = f"https://api.github.com/repos/{repo}/releases?per_page=50"
    req = urllib.request.Request(url, headers=get_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for rel in data:
                tag = str(rel.get("tag_name", "")).strip()
                if tag.isdigit():
                    tags.append(int(tag))
    except Exception as e:
        log(f"⚠️ Не удалось получить список релизов: {e}")

    # 2. Проверяем теги Git
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
    return 37


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


def create_release(token, repo, tag_num, changes):
    log(f"🚀 Создание GitHub Release с тегом '{tag_num}'...")
    url = f"https://api.github.com/repos/{repo}/releases"
    payload = {
        "tag_name": str(tag_num),
        "target_commitish": "main",
        "name": f"Заказ АА+",
        "body": f"Автоматический релиз Заказ АА+ (сборка {tag_num})\n\n"
                f"- Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
                f"- Архитектура: arm64-v8a\n"
                f"- Изменения: {changes}\n"
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


def update_gist_app_info(token, version, apk_url, changes):
    log(f"📝 Обновляем app_info.json в GitHub Gist ({GIST_ID})...")
    url = f"https://api.github.com/gists/{GIST_ID}"
    app_info = {
        "version": int(version),
        "apk_url": apk_url,
        "changes": changes,
        "updated_at": datetime.now().strftime("%d.%m.%Y %H:%M")
    }

    payload = {
        "files": {
            "app_info.json": {
                "content": json.dumps(app_info, ensure_ascii=False, indent=2)
            }
        }
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="PATCH",
        headers=get_headers(token)
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status in (200, 201):
                log(f"🎉 Файл app_info.json в Gist успешно обновлён (v{version})!")
                return True
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8")
        log(f"❌ Ошибка обновления app_info.json в Gist (HTTP {e.code}): {err_msg}")
    except Exception as ex:
        log(f"❌ Ошибка при запросе к Gist API: {ex}")
    return False


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    token = get_token(script_dir)
    if not token:
        log("❌ GitHub токен не найден в переменных окружения или в .gist_token")
        sys.exit(1)

    current_main_ver = get_current_app_version(script_dir)

    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        tag_num = int(sys.argv[1])
        log(f"Используем указанный номер релиза: {tag_num}")
    else:
        next_remote_tag = find_next_tag(token, REPO)
        tag_num = max(next_remote_tag, current_main_ver + 1)
        log(f"Определён следующий номер релиза: {tag_num} (текущий в main.py: {current_main_ver})")

    # 1. Автоматически увеличиваем APP_VERSION в main.py
    update_main_app_version(tag_num, script_dir)

    # Описание изменений
    default_changes = (
        f"Обновление Заказ АА+ (сборка {tag_num}):\n"
        f"- Актуальный каталог товаров и цен\n"
        f"- Новые и продленные акции\n"
        f"- Архитектура: arm64-v8a"
    )
    changes = os.environ.get("RELEASE_CHANGES") or default_changes

    # 2. Фиксируем и пушим код
    git_commit_and_push(token, REPO, tag_num)

    # 3. Собираем APK
    build_apk(tag_num)

    # 4. Создаём релиз
    release_data = create_release(token, REPO, tag_num, changes)

    # 5. Загружаем Zakaz_AA.apk
    upload_url = release_data.get("upload_url")
    download_url = upload_asset(token, upload_url, APK_PATH)

    # 6. Обновляем app_info.json в Gist
    update_gist_app_info(token, tag_num, download_url, changes)

    log("=" * 50)
    log(f"🎉 Выпуск обновления завершён успешно!")
    log(f"📱 Тег / Сборка: {tag_num}")
    log(f"🌐 Релиз: {release_data.get('html_url')}")
    log(f"📥 Скачать APK: {download_url}")
    log(f"⭐ Постоянная ссылка (latest): https://github.com/{REPO}/releases/latest/download/{ASSET_NAME}")
    log(f"ℹ️ app_info.json в Gist обновлён до v{tag_num}")
    log("=" * 50)


if __name__ == "__main__":
    main()

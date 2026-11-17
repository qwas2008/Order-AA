# Правила проекта

## 1. Сборка Android APK
По команде «Создай APK» или «Собери APK» всегда выполнять в терминале:
/Library/Frameworks/Python.framework/Versions/3.13/bin/flet build apk --arch arm64-v8a --exclude .git storage build --project "zakaz_aa_plus" --product "Заказ АА+" --org "com.anton"

## 2. Синхронизация каталога с GitHub Gist
По команде «Синхронизируй Gist» или «Обнови Gist» всегда выполнять в терминале:
GITHUB_TOKEN=$(cat .gist_token) /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 sync_to_gist.py

## 3. Автоматический выпуск обновления
По команде «Обнови релиз» или «Выпусти обновление» всегда выполнять в терминале:
GITHUB_TOKEN=$(cat .gist_token) /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 auto_release.py
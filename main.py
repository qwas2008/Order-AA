import flet as ft
import urllib.request
import json
import os
import hashlib
import subprocess
import sys
import time
from datetime import datetime

GIST_ID = "5503a740bfc7c96e8f76f7e7a3dae417"
APP_VERSION = 38
GIST_API_URL = f"https://api.github.com/gists/{GIST_ID}"
CATALOG_URL = f"https://gist.githubusercontent.com/qwas2008/{GIST_ID}/raw/catalog.json"
APP_INFO_URL = f"https://gist.githubusercontent.com/qwas2008/{GIST_ID}/raw/app_info.json"
DB_FILE = 'amway_db.json'
CART_FILE = 'cart_state.json'

BRAND_SYNONYMS = {
    "glister": "глистер",
    "nutrilite": "нутрилайт",
    "artistry": "артистри",
    "satinique": "сатиник",
    "icook": "айкук",
    "espring": "испринг еспринг",
    "dishdrops": "дишдропс",
    "amway": "амвей амвэй",
    "bodykey": "бодикей",
    "truvivity": "трувивити",
    "hymm": "химм",
}

# ==========================================
# 1. ЗАГРУЗКА БАЗЫ ДАННЫХ, СИНХРОНИЗАЦИЯ С СЕТЬЮ И КОРЗИНА
# ==========================================
def load_local_db():
    default_db = {
        "settings": {"rate": 0.205, "delivery": 10},
        "products": [],
        "promos": {},
        "catalog_hash": "",
        "last_sync": ""
    }
    try:
        if os.path.exists(DB_FILE):
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                default_db.update(data)
                if "settings" not in default_db or not isinstance(default_db["settings"], dict):
                    default_db["settings"] = {"rate": 0.205, "delivery": 10}
                if "products" not in default_db or not isinstance(default_db["products"], list):
                    default_db["products"] = []
                if "promos" not in default_db or not isinstance(default_db["promos"], dict):
                    default_db["promos"] = {}
    except Exception:
        pass
    return default_db


def save_db_to_disk(db_data):
    try:
        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(db_data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def normalize_remote_data(remote_data, existing_db):
    """Приводит скачанный из сети JSON (список или словарь) к формату amway_db.json."""
    existing_cats = {
        str(p.get('sku', '')): p.get('category', '')
        for p in existing_db.get('products', [])
        if isinstance(p, dict) and p.get('sku')
    }

    new_products = []
    new_promos = {}

    if isinstance(remote_data, dict):
        raw_list = remote_data.get('products', remote_data.get('catalog', []))
        if isinstance(remote_data.get('promos'), dict):
            new_promos.update(remote_data['promos'])
    elif isinstance(remote_data, list):
        raw_list = remote_data
    else:
        return None, None

    current_cat = ""
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        if item.get('type') == 'category':
            current_cat = str(item.get('name', '')).strip()
            continue

        sku = str(item.get('sku', '')).strip()
        if not sku:
            continue

        name = str(item.get('name', '')).strip()
        vol = str(item.get('vol', '')).strip()
        kzt = float(item.get('kzt', item.get('kzt_price', 0)) or 0)
        pv = float(item.get('pv', 0) or 0)
        cat = str(item.get('category', '')).strip() or current_cat or existing_cats.get(sku, '')

        new_products.append({
            "sku": sku,
            "name": name,
            "vol": vol,
            "kzt": kzt,
            "pv": pv,
            "category": cat
        })

        # Если акция зашита прямо в карточку товара в сетевом файле
        if 'promo_kzt_price' in item or 'promo_kzt' in item:
            p_kzt = float(item.get('promo_kzt', item.get('promo_kzt_price', kzt)) or kzt)
            p_pv = float(item.get('promo_pv', pv) or pv)
            p_start = str(item.get('promo_start', item.get('start', ''))).strip()
            p_end = str(item.get('promo_end', item.get('end', ''))).strip()
            new_promos[sku] = {
                "start": p_start,
                "end": p_end,
                "promo_kzt": p_kzt,
                "promo_pv": p_pv
            }

    return new_products, new_promos


def fetch_latest_catalog_bytes():
    """
    Загружает свежий catalog.json сначала через GitHub API (мгновенно, без 5-минутного кэша CDN),
    а при недоступности API — по прямой ссылке CATALOG_URL.
    """
    ts = int(time.time())
    # 1. Пробуем через GitHub Gist API (отдает обновления без задержки кэша)
    try:
        api_req = urllib.request.Request(
            f"{GIST_API_URL}?t={ts}",
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "AmwayApp-Client",
                "Cache-Control": "no-cache"
            }
        )
        with urllib.request.urlopen(api_req, timeout=4) as resp:
            gist_meta = json.loads(resp.read().decode('utf-8'))
        files = gist_meta.get("files", {})
        cat_file = files.get("catalog.json")
        if cat_file:
            if not cat_file.get("truncated") and cat_file.get("content"):
                return cat_file["content"].encode('utf-8')
            raw_commit_url = cat_file.get("raw_url")
            if raw_commit_url:
                raw_req = urllib.request.Request(
                    raw_commit_url,
                    headers={"User-Agent": "AmwayApp-Client", "Cache-Control": "no-cache"}
                )
                with urllib.request.urlopen(raw_req, timeout=4) as r_resp:
                    return r_resp.read()
    except Exception:
        pass

    # 2. Фолбэк на прямую ссылку
    req = urllib.request.Request(
        f"{CATALOG_URL}?t={ts}",
        headers={"User-Agent": "Mozilla/5.0", "Cache-Control": "no-cache"}
    )
    with urllib.request.urlopen(req, timeout=4) as response:
        return response.read()


def sync_db_from_network(db_data):
    """
    Проверяет наличие нового каталога в Gist.
    Если интернет есть и файл обновился — скачивает и сохраняет в локальный amway_db.json.
    Если интернета нет — тихо возвращает локальную базу без ошибок.
    """
    try:
        raw_bytes = fetch_latest_catalog_bytes()
        new_hash = hashlib.sha256(raw_bytes).hexdigest()
        remote_json = json.loads(raw_bytes.decode('utf-8'))
        new_products, new_promos = normalize_remote_data(remote_json, db_data)

        if new_products:
            is_changed = (new_hash != db_data.get("catalog_hash")) or not db_data.get("products")
            db_data["products"] = new_products
            db_data["promos"] = new_promos
            db_data["catalog_hash"] = new_hash
            db_data["last_sync"] = datetime.now().strftime("%d.%m.%Y %H:%M")
            if is_changed:
                save_db_to_disk(db_data)
            return True, is_changed
    except Exception:
        pass
    return False, False


db = load_local_db()
sync_db_from_network(db)


def save_settings(rate, delivery):
    db['settings']['rate'] = rate
    db['settings']['delivery'] = delivery
    save_db_to_disk(db)


def save_cart_state(cart):
    try:
        data = {item['sku']: item['qty'] for item in cart}
        with open(CART_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def load_cart_state(products_by_sku):
    restored = []
    try:
        if os.path.exists(CART_FILE):
            with open(CART_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for sku, qty in data.items():
                if sku in products_by_sku and int(qty) > 0:
                    restored.append({
                        'sku': sku,
                        'qty': int(qty),
                        'product': products_by_sku[sku]
                    })
    except Exception:
        pass
    return restored


def parse_date_safe(date_str):
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    return None


def build_search_index(product):
    sku = str(product.get('sku', ''))
    name = str(product.get('name', ''))
    vol = str(product.get('vol', ''))
    cat = str(product.get('category', ''))
    raw_str = f"{sku} {name} {vol} {cat}".lower().replace('ё', 'е')
    alnum_str = ''.join(c for c in raw_str if c.isalnum())
    extra = []
    for lat, cyr in BRAND_SYNONYMS.items():
        if lat in alnum_str:
            extra.append(cyr.replace(' ', ''))
    if extra:
        alnum_str += ''.join(extra)
    return alnum_str


# ==========================================
# 2. ОСНОВНОЙ КЛАСС ПРИЛОЖЕНИЯ
# ==========================================
def main(page: ft.Page):
    page.title = "Заказ АА+"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.padding = 0
    try:
        page.window.width = 400
        page.window.height = 800
    except Exception:
        page.window_width = 400
        page.window_height = 800

    current_date = datetime.now().date()
    products_by_sku = {p['sku']: p for p in db['products']}
    cart = load_cart_state(products_by_sku)

    catalog_list = ft.ListView(expand=True, spacing=10)
    promo_list = ft.ListView(expand=True, spacing=10)
    cart_items_list = ft.ListView(expand=True, spacing=10)

    total_rub_text = ft.Text("К оплате: 0.00 RUB", size=18, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900)
    total_sub_text = ft.Text("База: 0.00 KZT  |  Баллы: 0.00 PV", size=14, weight=ft.FontWeight.W_600, color=ft.Colors.BLUE_GREY_700)

    def get_price_info(product, order_date):
        sku = product['sku']
        base_kzt = float(product.get('kzt', product.get('kzt_price', 0)) or 0)
        base_pv = float(product.get('pv', 0) or 0)

        is_promo = False
        final_kzt = base_kzt
        final_pv = base_pv
        promo_label = ""

        if sku in db.get('promos', {}):
            promo = db['promos'][sku]
            start_d = parse_date_safe(promo.get('start', ''))
            end_d = parse_date_safe(promo.get('end', ''))

            active = True
            if start_d and order_date < start_d:
                active = False
            if end_d and order_date > end_d:
                active = False

            if active:
                is_promo = True
                final_kzt = float(promo.get('promo_kzt', promo.get('promo_kzt_price', base_kzt)) or base_kzt)
                final_pv = float(promo.get('promo_pv', base_pv) or base_pv)
                start_fmt = start_d.strftime("%d.%m.%y") if start_d else promo.get('start', '')
                end_fmt = end_d.strftime("%d.%m.%y") if end_d else promo.get('end', '')
                if start_fmt and end_fmt:
                    promo_label = f"Акция: {start_fmt} - {end_fmt}"
                elif end_fmt:
                    promo_label = f"Акция до {end_fmt}"
                else:
                    promo_label = "Акция"

        rate = float(db['settings']['rate'])
        delivery = float(db['settings']['delivery']) / 100.0
        final_rub = round(final_kzt * rate * (1 + delivery), 2)
        base_rub = round(base_kzt * rate * (1 + delivery), 2)

        return {
            "is_promo": is_promo,
            "kzt": final_kzt,
            "rub": final_rub,
            "pv": final_pv,
            "base_kzt": base_kzt,
            "base_rub": base_rub,
            "base_pv": base_pv,
            "promo_label": promo_label
        }

    def update_badge():
        total_items = sum(item['qty'] for item in cart)
        if total_items > 0:
            page.navigation_bar.destinations[2].label = f"Корзина ({total_items})"
        else:
            page.navigation_bar.destinations[2].label = "Корзина"
        page.navigation_bar.update()

    # ----------------------------------------
    # КАРТОЧКА ТОВАРА В КАТАЛОГЕ И АКЦИЯХ
    # ----------------------------------------
    class ProductCard(ft.Card):
        def __init__(self, product):
            super().__init__()
            self.product = product
            self.sku = product['sku']
            self.name = product['name']
            self.vol = product['vol']

            self.search_index = build_search_index(product)

            self.qty_text = ft.Text("0", size=16, weight=ft.FontWeight.BOLD)

            self.row_qty = ft.Row([
                ft.IconButton(ft.Icons.REMOVE_CIRCLE_OUTLINE, icon_color=ft.Colors.RED_400, on_click=self.dec_qty),
                self.qty_text,
                ft.IconButton(ft.Icons.ADD_CIRCLE_OUTLINE, icon_color=ft.Colors.GREEN_400, on_click=self.inc_qty),
            ], alignment=ft.MainAxisAlignment.END, spacing=0)

            info = get_price_info(self.product, current_date)
            name_str = f"🔥 {self.name}" if info['is_promo'] else self.name

            rub_str = f"{info['rub']:,.2f}".replace(',', ' ')
            kzt_str = f"{info['kzt']:,.0f}".replace(',', ' ')

            self.price_text = ft.Text(
                f"{rub_str} RUB | {kzt_str} ₸ | {info['pv']} PV",
                size=12,
                no_wrap=True,
                color=ft.Colors.GREEN_700 if info['is_promo'] else ft.Colors.BLUE_GREY_800,
                weight=ft.FontWeight.BOLD
            )

            self.base_price_text = None

            col_controls = [
                ft.Text(f"{self.sku}  •  {self.vol}", size=12, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_700),
                ft.Text(name_str, size=14, no_wrap=False),
            ]

            if info['is_promo']:
                col_controls.append(
                    ft.Text(info['promo_label'], size=12, color=ft.Colors.RED_600, weight=ft.FontWeight.BOLD)
                )
                if info['base_kzt'] != info['kzt']:
                    base_rub_str = f"{info['base_rub']:,.2f}".replace(',', ' ')
                    base_kzt_str = f"{info['base_kzt']:,.0f}".replace(',', ' ')
                    self.base_price_text = ft.Text(
                        f"Обычная: {base_rub_str} RUB | {base_kzt_str} ₸",
                        size=11,
                        color=ft.Colors.GREY_600
                    )

            col_controls.append(self.price_text)
            if self.base_price_text is not None:
                col_controls.append(self.base_price_text)

            self.content = ft.Container(
                content=ft.Row([
                    ft.Column(col_controls, spacing=3, expand=True),
                    self.row_qty
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                padding=10
            )
            self.sync_state(update_ui=False)

        def update_price_label(self):
            info = get_price_info(self.product, current_date)
            rub_str = f"{info['rub']:,.2f}".replace(',', ' ')
            kzt_str = f"{info['kzt']:,.0f}".replace(',', ' ')
            self.price_text.value = f"{rub_str} RUB | {kzt_str} ₸ | {info['pv']} PV"
            if self.base_price_text is not None:
                base_rub_str = f"{info['base_rub']:,.2f}".replace(',', ' ')
                base_kzt_str = f"{info['base_kzt']:,.0f}".replace(',', ' ')
                self.base_price_text.value = f"Обычная: {base_rub_str} RUB | {base_kzt_str} ₸"

        def sync_state(self, update_ui=True):
            qty = 0
            for item in cart:
                if item['sku'] == self.sku:
                    qty = item['qty']
                    break

            self.qty_text.value = str(qty)
            self.color = ft.Colors.GREEN_50 if qty > 0 else ft.Colors.WHITE

            if update_ui:
                self.update()

        def inc_qty(self, e):
            found = False
            for item in cart:
                if item['sku'] == self.sku:
                    item['qty'] += 1
                    found = True
                    break

            if not found:
                cart.append({'sku': self.sku, 'qty': 1, 'product': self.product})

            save_cart_state(cart)

            for ctrl in cards_by_sku.get(self.sku, []):
                ctrl.sync_state(update_ui=False)

            update_badge()
            page.update()

        def dec_qty(self, e):
            for item in cart:
                if item['sku'] == self.sku:
                    if item['qty'] > 0:
                        item['qty'] -= 1
                    if item['qty'] <= 0:
                        cart.remove(item)
                    break

            save_cart_state(cart)

            for ctrl in cards_by_sku.get(self.sku, []):
                ctrl.sync_state(update_ui=False)

            update_badge()
            page.update()

    # --- ПРЕДВАРИТЕЛЬНАЯ ОТРИСОВКА КАТАЛОГА И АКЦИЙ ---
    all_cards = []
    promo_cards = []
    cards_by_sku = {}

    no_results_card = ft.Container(
        content=ft.Row([
            ft.Text("Ничего не найдено", size=16, color=ft.Colors.GREY_600, weight=ft.FontWeight.W_500)
        ], alignment=ft.MainAxisAlignment.CENTER),
        padding=30
    )

    no_promos_placeholder = ft.Container(
        content=ft.Row([
            ft.Text("Активных акций пока нет 😔", size=18, color=ft.Colors.GREY_600)
        ], alignment=ft.MainAxisAlignment.CENTER),
        expand=True,
        padding=30
    )

    promo_view_container = ft.Container(expand=True, visible=False)

    def build_catalog_and_promo_cards():
        all_cards.clear()
        promo_cards.clear()
        cards_by_sku.clear()

        for p in db['products']:
            c_card = ProductCard(p)
            all_cards.append(c_card)
            cards_by_sku.setdefault(p['sku'], []).append(c_card)

            info = get_price_info(p, current_date)
            if info['is_promo']:
                p_card = ProductCard(p)
                promo_cards.append(p_card)
                cards_by_sku.setdefault(p['sku'], []).append(p_card)

        catalog_list.controls = list(all_cards)
        promo_list.controls = list(promo_cards)

        if not promo_cards:
            promo_view_container.content = no_promos_placeholder
        else:
            promo_view_container.content = ft.Column([promo_list], expand=True)

    build_catalog_and_promo_cards()

    # ----------------------------------------
    # ЭКРАН 1: НАСТРОЙКИ (SETTINGS)
    # ----------------------------------------
    rate_input = ft.TextField(label="Курс RUB/KZT", value=str(db['settings']['rate']), keyboard_type=ft.KeyboardType.NUMBER)
    delivery_input = ft.TextField(label="Доставка (%)", value=str(db['settings']['delivery']), keyboard_type=ft.KeyboardType.NUMBER)

    sync_info_text = ft.Text(
        f"Товаров в базе: {len(db['products'])} | Акций: {len(promo_cards)}\n"
        f"Обновлено: {db.get('last_sync') or 'локальная база'}",
        size=12,
        color=ft.Colors.GREY_700,
        text_align=ft.TextAlign.CENTER
    )

    def save_settings_click(e):
        try:
            r = float(rate_input.value.replace(',', '.'))
            d = float(delivery_input.value.replace(',', '.'))
            save_settings(r, d)
            for ctrl in all_cards:
                ctrl.update_price_label()
            for ctrl in promo_cards:
                ctrl.update_price_label()
            build_cart_ui()
            page.open(ft.SnackBar(ft.Text("Настройки сохранены и цены пересчитаны!")))
            page.update()
        except ValueError:
            page.open(ft.SnackBar(ft.Text("Ошибка! Введите корректные числа.")))
            page.update()

    def manual_sync_click(e):
        ok, changed = sync_db_from_network(db)
        if ok:
            products_by_sku.clear()
            for p in db['products']:
                products_by_sku[p['sku']] = p
            for item in cart:
                if item['sku'] in products_by_sku:
                    item['product'] = products_by_sku[item['sku']]
            build_catalog_and_promo_cards()
            filter_catalog()
            build_cart_ui()
            sync_info_text.value = (
                f"Товаров в базе: {len(db['products'])} | Акций: {len(promo_cards)}\n"
                f"Обновлено: {db.get('last_sync') or 'только что'}"
            )
            msg = f"База обновлена! Товаров: {len(db['products'])}, акций: {len(promo_cards)}" if changed else f"База актуальна (товаров: {len(db['products'])}, акций: {len(promo_cards)})"
            page.open(ft.SnackBar(ft.Text(msg)))
        else:
            page.open(ft.SnackBar(ft.Text("Нет связи с сервером. Работаем на локальной базе.")))
        page.update()

    def check_for_updates(silent=False):
        def _fetch():
            try:
                ts = int(time.time())
                url = f"{APP_INFO_URL}?t={ts}"
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "Mozilla/5.0", "Cache-Control": "no-cache"}
                )
                with urllib.request.urlopen(req, timeout=4) as response:
                    raw_data = response.read().decode('utf-8')
                data = json.loads(raw_data)

                remote_version = int(data.get("version", 0))
                apk_url = str(data.get("apk_url", "")).strip()
                changes = str(data.get("changes", "Доступна новая версия приложения.")).strip()

                if remote_version > APP_VERSION:
                    def download_click(e):
                        try:
                            page.close(dlg)
                        except Exception:
                            pass
                        if apk_url:
                            page.launch_url(apk_url)

                    def close_click(e):
                        try:
                            page.close(dlg)
                        except Exception:
                            pass

                    dlg = ft.AlertDialog(
                        title=ft.Text("Доступно обновление!", weight=ft.FontWeight.BOLD),
                        content=ft.Column([
                            ft.Text(f"Новая версия: {remote_version} (текущая: {APP_VERSION})", weight=ft.FontWeight.W_500),
                            ft.Divider(height=10),
                            ft.Text(changes, size=13),
                        ], tight=True, spacing=6),
                        actions=[
                            ft.TextButton("Позже", on_click=close_click),
                            ft.ElevatedButton(
                                "Скачать",
                                icon=ft.Icons.DOWNLOAD,
                                on_click=download_click,
                                style=ft.ButtonStyle(bgcolor=ft.Colors.BLUE_700, color=ft.Colors.WHITE)
                            ),
                        ],
                        actions_alignment=ft.MainAxisAlignment.END,
                    )

                    try:
                        page.open(dlg)
                    except Exception:
                        page.dialog = dlg
                        dlg.open = True
                        page.update()
                else:
                    if not silent:
                        page.open(ft.SnackBar(ft.Text(f"У вас установлена последняя версия (v{APP_VERSION})!")))
                        page.update()
            except Exception:
                if not silent:
                    page.open(ft.SnackBar(ft.Text("Не удалось проверить обновление приложения.")))
                    page.update()

        import threading
        threading.Thread(target=_fetch, daemon=True).start()

    settings_view = ft.Container(
        content=ft.Column([
            ft.Text("Настройки расчета", size=24, weight=ft.FontWeight.BOLD),
            rate_input,
            delivery_input,
            ft.ElevatedButton("Сохранить", on_click=save_settings_click, width=300),
            ft.Divider(height=10),
            ft.OutlinedButton("Обновить прайс", icon=ft.Icons.CLOUD_SYNC, on_click=manual_sync_click, width=300),
            sync_info_text,
            ft.Divider(height=10),
            ft.OutlinedButton("Обновить приложение", icon=ft.Icons.SYSTEM_UPDATE, on_click=lambda e: check_for_updates(silent=False), width=300),
            ft.Text(f"Версия приложения: {APP_VERSION}", size=12, color=ft.Colors.GREY_700, text_align=ft.TextAlign.CENTER)
        ], alignment=ft.MainAxisAlignment.START, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=20),
        padding=20,
        visible=False
    )

    # ----------------------------------------
    # ЭКРАН 2: ПОИСК КАТАЛОГА (CATALOG)
    # ----------------------------------------
    def filter_catalog(e=None):
        raw_q = (search_bar.value or "").lower().replace('ё', 'е')
        tokens = [''.join(c for c in w if c.isalnum()) for w in raw_q.split()]
        tokens = [t for t in tokens if t]

        if not tokens:
            catalog_list.controls = list(all_cards)
        else:
            matched = [
                card for card in all_cards
                if all(t in card.search_index for t in tokens)
            ]
            catalog_list.controls = matched if matched else [no_results_card]

        page.update()

    def clear_search_click(e):
        search_bar.value = ""
        catalog_list.controls = list(all_cards)
        page.update()

    search_bar = ft.TextField(
        hint_text="Поиск по артикулу или названию...",
        prefix_icon=ft.Icons.SEARCH,
        on_change=filter_catalog,
        expand=True
    )

    clear_search_btn = ft.IconButton(
        icon=ft.Icons.CLEAR,
        icon_color=ft.Colors.GREY_700,
        on_click=clear_search_click,
        tooltip="Сбросить поиск"
    )

    catalog_view = ft.Column([
        ft.Row([search_bar, clear_search_btn], spacing=4),
        catalog_list
    ], expand=True, visible=True)

    # ----------------------------------------
    # ЭКРАН 3: КОРЗИНА И ЧЕК (CART)
    # ----------------------------------------
    date_text = ft.Text(f"Дата накладной: {current_date.strftime('%d.%m.%Y')}", size=15, color=ft.Colors.BLUE_GREY_600)

    def build_cart_ui():
        cart_items_list.controls.clear()

        t_rub = 0
        t_kzt = 0
        t_pv = 0

        for item in cart:
            p = item['product']
            info = get_price_info(p, current_date)

            t_rub += info['rub'] * item['qty']
            t_kzt += info['kzt'] * item['qty']
            t_pv += info['pv'] * item['qty']

            promo_icon = ft.Icon(ft.Icons.LOCAL_FIRE_DEPARTMENT, color=ft.Colors.RED, size=18) if info['is_promo'] else ft.Container()

            rub_str = f"{info['rub']:,.2f}".replace(',', ' ')
            sum_rub_str = f"{info['rub'] * item['qty']:,.2f}".replace(',', ' ')

            row = ft.Card(
                color=ft.Colors.GREEN_50,
                content=ft.Container(
                    content=ft.Column([
                        ft.Row([
                            promo_icon,
                            ft.Text(f"{p['sku']} | {p['name']} ({p['vol']})", weight=ft.FontWeight.BOLD, size=14, expand=True, no_wrap=False)
                        ], vertical_alignment=ft.CrossAxisAlignment.START),
                        ft.Row([
                            ft.Text(f"{rub_str} RUB × {item['qty']} = {sum_rub_str} RUB", size=13, color=ft.Colors.GREY_800, weight=ft.FontWeight.W_500),
                            ft.Row([
                                ft.IconButton(ft.Icons.REMOVE_CIRCLE_OUTLINE, icon_color=ft.Colors.RED_400, on_click=lambda e, i=item: change_qty_in_cart(i, -1)),
                                ft.Text(f"{item['qty']}", size=16, weight=ft.FontWeight.BOLD),
                                ft.IconButton(ft.Icons.ADD_CIRCLE_OUTLINE, icon_color=ft.Colors.GREEN_400, on_click=lambda e, i=item: change_qty_in_cart(i, 1)),
                            ], spacing=0)
                        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
                    ], spacing=4),
                    padding=10
                )
            )
            cart_items_list.controls.append(row)

        total_rub_text.value = f"К оплате: {t_rub:,.2f} RUB".replace(',', ' ')
        total_sub_text.value = f"База: {t_kzt:,.2f} KZT  |  Баллы: {t_pv:,.2f} PV".replace(',', ' ')
        update_badge()

    def change_qty_in_cart(item, delta):
        item['qty'] += delta
        if item['qty'] <= 0:
            cart.remove(item)

        save_cart_state(cart)

        for ctrl in cards_by_sku.get(item['sku'], []):
            ctrl.sync_state(update_ui=False)

        build_cart_ui()
        page.update()

    def clear_cart_click(e):
        cart.clear()
        save_cart_state(cart)

        for ctrl in all_cards:
            if ctrl.qty_text.value != "0":
                ctrl.sync_state(update_ui=False)
        for ctrl in promo_cards:
            if ctrl.qty_text.value != "0":
                ctrl.sync_state(update_ui=False)

        build_cart_ui()
        try:
            page.open(ft.SnackBar(ft.Text("Корзина очищена!"), duration=1500))
        except Exception:
            pass
        page.update()

    def generate_receipt_text():
        if not cart:
            return "Корзина пуста."

        lines = [
            f"📝 Ваш заказ ({current_date.strftime('%d.%m.%Y')}):\n"
        ]

        total_kzt = 0
        total_rub = 0
        total_pv = 0

        for i, item in enumerate(cart, 1):
            p = item['product']
            qty = item['qty']
            info = get_price_info(p, current_date)

            sum_kzt = info['kzt'] * qty
            sum_rub = info['rub'] * qty
            sum_pv = info['pv'] * qty

            total_kzt += sum_kzt
            total_rub += sum_rub
            total_pv += sum_pv

            marker = "🔥 " if info['is_promo'] else ""

            lines.append(f"{i}. {marker}[{p['sku']}] {p['name']} ({p['vol']})")
            lines.append(f"   {qty} шт. х {info['rub']:,.2f} RUB = {sum_rub:,.2f} RUB")

        lines.append(f"\n💰 Итого к оплате: {total_rub:,.2f} RUB ({total_kzt:,.2f} KZT)")
        lines.append(f"💎 Итого баллов: {total_pv:,.2f} PV")

        return "\n".join(lines).replace(',', ' ')

    def copy_receipt(e):
        text = generate_receipt_text()
        try:
            page.set_clipboard(text)
        except Exception:
            pass
        if sys.platform == "darwin":
            try:
                p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
                p.communicate(input=text.encode("utf-8"))
            except Exception:
                pass

    copy_btn = ft.ElevatedButton("Копировать", icon=ft.Icons.CONTENT_COPY, on_click=copy_receipt, style=ft.ButtonStyle(bgcolor=ft.Colors.BLUE_700, color=ft.Colors.WHITE))
    clear_btn = ft.OutlinedButton("Очистить", icon=ft.Icons.DELETE_OUTLINE, icon_color=ft.Colors.RED_500, style=ft.ButtonStyle(color=ft.Colors.RED_500), on_click=clear_cart_click)

    buttons_row = ft.Row([copy_btn, clear_btn], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)

    cart_view = ft.Column([
        date_text,
        cart_items_list,
        ft.Divider(height=1),
        ft.Column([total_rub_text, total_sub_text], spacing=2),
        buttons_row
    ], expand=True, spacing=8, visible=False)

    # ----------------------------------------
    # НАВИГАЦИЯ (BOTTOM APP BAR)
    # ----------------------------------------
    def change_tab(e):
        idx = e.control.selected_index
        catalog_view.visible = (idx == 0)
        promo_view_container.visible = (idx == 1)
        cart_view.visible = (idx == 2)
        settings_view.visible = (idx == 3)

        if idx == 2:
            build_cart_ui()

        page.update()

    total_init = sum(item['qty'] for item in cart)
    cart_label_init = f"Корзина ({total_init})" if total_init > 0 else "Корзина"

    page.navigation_bar = ft.NavigationBar(
        destinations=[
            ft.NavigationBarDestination(icon=ft.Icons.SEARCH, label="Каталог"),
            ft.NavigationBarDestination(icon=ft.Icons.LOCAL_FIRE_DEPARTMENT, label="Акции"),
            ft.NavigationBarDestination(icon=ft.Icons.SHOPPING_CART, label=cart_label_init),
            ft.NavigationBarDestination(icon=ft.Icons.SETTINGS, label="Настройки"),
        ],
        on_change=change_tab
    )

    page.add(
        ft.SafeArea(
            ft.Container(
                content=ft.Stack([
                    catalog_view,
                    promo_view_container,
                    cart_view,
                    settings_view
                ]),
                expand=True,
                padding=10
            ),
            expand=True
        )
    )

    check_for_updates(silent=True)


ft.app(target=main)

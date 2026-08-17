# PineOak GUIマニュアル用スクリーンショット撮影スクリプト
#
# 背景: Claude Browser(mcp__Claude_Browser__computer)のscreenshotは、
#   ブラウザペインが画面上に表示されていないと撮影できず、しかも結果を
#   画像ファイルとして保存する手段がない。そのためPlaywrightで直接
#   headless Chromiumを操作し、PNGファイルとして保存する。
#
# 前提: 事前に main.py を起動しておくこと（例: preview_start や
#   `python -X utf8 main.py` を実行し、http://localhost:8765 で待受）。
#   ※ main.py はprint文にem dash(—)を含むため、cp932コンソールでは
#      `python -X utf8 main.py` のようにUTF-8モードで起動しないと落ちる。
#
# 初回セットアップ（このPC環境にはNode.js/LibreOfficeが無いため）:
#   python -m pip install playwright
#   python -m playwright install chromium
#
# 使い方: 対象ページのURLとセクションのCSSセレクタを書き換えて実行する。
#   PineOakの各ウィザードHTML（dic_wizard.html, ebsd_wizard.html,
#   heaviside_wizard.html 等）は概ね同じCSS構造
#   （.left-panel / .right-panel .section / footer）を持っているはずなので、
#   まずこのスクリプトをコピーしてURLだけ変えて試すとよい。
#   構造が異なる場合はブラウザのread_page(filter=all)で先に構造を確認すること。

from playwright.sync_api import sync_playwright

URL = "http://localhost:8765/dic_wizard.html"   # 対象ウィザードのURL
OUT = r"E:\Onedriveと同期しないフォルダ\PineOak\manual_tools\screenshots"  # 出力先（都度作成する）
LEFT_PANEL_SELECTOR = ".left-panel"
SECTION_SELECTOR = ".right-panel .section"
FOOTER_SELECTOR = "footer, .footer, .toolbar"

import os
os.makedirs(OUT, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1300, "height": 900})
    page.goto(URL)
    page.wait_for_timeout(400)

    # 左パネル（ファイル選択など）
    page.locator(LEFT_PANEL_SELECTOR).screenshot(path=f"{OUT}/left_panel.png")

    # 右パネルの各セクションを個別に撮影
    # （ページ全体はoverflow:hiddenで内部パネルだけがスクロールする構造の
    #   ことが多く、fullPageスクリーンショットでは全部映らないため、
    #   要素単位でscroll_into_view_if_needed()してから撮る）
    titles = page.locator(f"{SECTION_SELECTOR} .section-title").all_inner_texts()
    print("SECTIONS:", titles)

    sections = page.locator(SECTION_SELECTOR)
    count = sections.count()
    for i in range(count):
        sec = sections.nth(i)
        try:
            sec.scroll_into_view_if_needed()
            page.wait_for_timeout(150)
            sec.screenshot(path=f"{OUT}/section_{i}.png")
        except Exception as e:
            print(f"section {i} failed:", e)

    # 画面下部の実行ボタン列
    page.locator(FOOTER_SELECTOR).first.screenshot(path=f"{OUT}/footer.png")

    browser.close()

print("done:", OUT)

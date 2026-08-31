import os
import time

from playwright.sync_api import Error, Page, sync_playwright


def verify_scroll_containment(page: Page, width: int, height: int) -> None:
    page.set_viewport_size({'width': width, 'height': height})
    thread = page.locator('.thread')
    thread.evaluate(
        """element => {
            let filler = element.querySelector('[data-scroll-test]');
            if (!filler) {
                filler = document.createElement('div');
                filler.dataset.scrollTest = 'true';
                filler.style.height = '2000px';
                element.append(filler);
            }
            element.scrollTop = 0;
        }"""
    )

    metrics = page.evaluate(
        """() => {
            const appShell = document.querySelector('.app-shell');
            const chatPanel = document.querySelector('.chat-panel');
            const thread = document.querySelector('.thread');
            const filler = document.querySelector('[data-scroll-test]');
            return {
                appShellHeight: appShell.clientHeight,
                chatPanelHeight: chatPanel.clientHeight,
                threadClientHeight: thread.clientHeight,
                threadScrollHeight: thread.scrollHeight,
                fillerHeight: filler.getBoundingClientRect().height,
                pageScrollHeight: document.scrollingElement.scrollHeight,
                viewportHeight: window.innerHeight,
                bodyOverflowY: getComputedStyle(document.body).overflowY,
                loading: document.querySelector('[aria-busy="true"]') !== null,
            };
        }"""
    )
    assert metrics['threadScrollHeight'] > metrics['threadClientHeight'], metrics
    assert metrics['bodyOverflowY'] == 'hidden', metrics
    assert metrics['pageScrollHeight'] <= metrics['viewportHeight'], metrics

    thread.hover()
    page.mouse.wheel(0, 600)
    page.wait_for_timeout(100)

    assert thread.evaluate('element => element.scrollTop') > 0
    assert page.evaluate('window.scrollY') == 0


def main() -> None:
    web_url = os.getenv('MEDIPET_WEB_URL', 'http://localhost:3000')

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})

        deadline = time.monotonic() + 30
        while True:
            try:
                page.goto(web_url, wait_until='networkidle', timeout=60_000)
                break
            except Error:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.5)

        page.locator('.thread').wait_for()
        verify_scroll_containment(page, width=1440, height=1000)
        verify_scroll_containment(page, width=390, height=844)
        browser.close()

    print('MediPet scroll containment passed at desktop and mobile viewports')


if __name__ == '__main__':
    main()

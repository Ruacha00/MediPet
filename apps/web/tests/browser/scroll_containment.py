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


def verify_interaction_budget(page: Page) -> dict[str, float | str]:
    metrics = page.evaluate(
        """async () => {
            const panel = document.querySelector('.chat-panel');
            const textarea = document.querySelector('textarea');
            const thread = document.querySelector('.thread');
            const valueSetter = Object.getOwnPropertyDescriptor(
                HTMLTextAreaElement.prototype,
                'value',
            ).set;
            const inputFrames = [];

            for (let index = 0; index < 24; index += 1) {
                await new Promise(requestAnimationFrame);
                const startedAt = performance.now();
                valueSetter.call(textarea, '流畅度测试'.repeat(index + 1));
                textarea.dispatchEvent(new Event('input', { bubbles: true }));
                await new Promise(requestAnimationFrame);
                inputFrames.push(performance.now() - startedAt);
            }

            const filler = document.createElement('div');
            filler.style.height = '5000px';
            thread.append(filler);
            thread.scrollTop = 0;
            const scrollFrames = [];
            let previousFrame = performance.now();
            thread.scrollTo({ top: 5000, behavior: 'smooth' });
            await new Promise((resolve) => {
                const sampleFrame = (now) => {
                    scrollFrames.push(now - previousFrame);
                    previousFrame = now;
                    if (scrollFrames.length >= 60) resolve();
                    else requestAnimationFrame(sampleFrame);
                };
                requestAnimationFrame(sampleFrame);
            });
            filler.remove();

            inputFrames.sort((left, right) => left - right);
            scrollFrames.sort((left, right) => left - right);
            return {
                backdropFilter: getComputedStyle(panel).backdropFilter,
                inputP95: inputFrames[Math.floor(inputFrames.length * 0.95)],
                scrollP95: scrollFrames[Math.floor(scrollFrames.length * 0.95)],
            };
        }"""
    )

    assert metrics['backdropFilter'] in ('', 'none'), metrics
    assert metrics['inputP95'] <= 50, metrics
    assert metrics['scrollP95'] <= 25, metrics
    return metrics


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
        page.set_viewport_size({'width': 1440, 'height': 1000})
        interaction_metrics = verify_interaction_budget(page)
        browser.close()

    print(
        'MediPet scroll containment and interaction budget passed; '
        f'metrics={interaction_metrics}'
    )


if __name__ == '__main__':
    main()

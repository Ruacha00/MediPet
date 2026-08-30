import json
import os
import time
from pathlib import Path

from playwright.sync_api import Error, Route, sync_playwright


def _stream(*events: dict | str) -> str:
    return ''.join(
        f'data: {event if isinstance(event, str) else json.dumps(event)}\n\n'
        for event in events
    )


def main() -> None:
    browser_errors: list[str] = []
    screenshot = Path(__file__).with_name('medipet-smoke.png')
    web_url = os.getenv('MEDIPET_WEB_URL', 'http://localhost:3000')
    responses = iter(
        [
            _stream(
                {'type': 'start', 'messageId': 'message-fake'},
                {'type': 'text-start', 'id': 'text-fake'},
                {
                    'type': 'text-delta',
                    'id': 'text-fake',
                    'delta': '可以先整理症状和时间线。',
                },
                {'type': 'text-end', 'id': 'text-fake'},
                {'type': 'finish', 'finishReason': 'stop'},
                '[DONE]',
            ),
            _stream(
                {'type': 'start', 'messageId': 'message-error'},
                {'type': 'error', 'errorText': '模型服务暂时不可用，请稍后重试。'},
                '[DONE]',
            ),
        ]
    )

    def fulfill_chat(route: Route) -> None:
        route.fulfill(status=200, content_type='text/event-stream', body=next(responses))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.route('**/v1/chat/turns', fulfill_chat)
        page.on(
            'console',
            lambda message: browser_errors.append(message.text)
            if message.type == 'error'
            else None,
        )

        deadline = time.monotonic() + 30
        while True:
            try:
                page.goto(web_url)
                break
            except Error:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.5)
        page.wait_for_load_state('networkidle')
        page.get_by_role('heading', name='把复杂的门诊流程，变成一次从容的对话。').wait_for()
        page.get_by_text('医院数据', exact=True).wait_for()
        page.get_by_text('未配置', exact=True).wait_for()

        page.get_by_role('button', name='整理症状').click()
        page.get_by_text('可以先整理症状和时间线。', exact=True).wait_for()

        composer = page.get_by_role('textbox', name='输入就诊需求')
        composer.fill('再次发送')
        page.get_by_role('button', name='发送消息').click()
        page.get_by_text('连接暂时中断，请检查后端服务后重试。', exact=True).wait_for()
        assert '模型服务暂时不可用' not in page.locator('body').inner_text()

        page.screenshot(path=str(screenshot), full_page=True)
        assert not browser_errors, browser_errors
        browser.close()

    print(f'MediPet browser smoke passed; screenshot={screenshot}')


if __name__ == '__main__':
    main()

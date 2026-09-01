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
    history_screenshot = Path(__file__).with_name('history-management-smoke.png')
    web_url = os.getenv('MEDIPET_WEB_URL', 'http://localhost:3000')
    seen_visit_matter_ids: list[str] = []
    archive_conflict = False
    active_visit_matter_ids: list[str] = []
    archived_visit_matter_ids: list[str] = []
    visit_titles = {
        'visit-matter-demo': '初次咨询',
        'visit-matter-new': '新的就诊事项',
    }
    markdown_answer = (
        '## 就诊准备\n\n'
        '- 记录症状出现时间\n'
        '- 整理当前用药\n\n'
        '如需配置，请检查 `MEDIPET_API_URL`，并查看 [帮助](https://example.com/help)。'
    )
    responses = iter(
        [
            _stream(
                {'type': 'start', 'messageId': 'message-fake'},
                {'type': 'text-start', 'id': 'text-fake'},
                {
                    'type': 'text-delta',
                    'id': 'text-fake',
                    'delta': markdown_answer,
                },
                {'type': 'text-end', 'id': 'text-fake'},
                {'type': 'finish', 'finishReason': 'stop'},
                '[DONE]',
            ),
            _stream(
                {'type': 'start', 'messageId': 'message-new-visit'},
                {'type': 'text-start', 'id': 'text-new-visit'},
                {
                    'type': 'text-delta',
                    'id': 'text-new-visit',
                    'delta': '这是新的独立就诊事项。',
                },
                {'type': 'text-end', 'id': 'text-new-visit'},
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
        seen_visit_matter_ids.append(route.request.post_data_json['visit_matter_id'])
        route.fulfill(status=200, content_type='text/event-stream', body=next(responses))

    def fulfill_visit_matter(route: Route) -> None:
        payload = route.request.post_data_json
        assert payload['participant_id'] == 'participant-demo'
        active_visit_matter_ids.insert(0, 'visit-matter-new')
        route.fulfill(
            status=201,
            content_type='application/json',
            body=json.dumps(
                {
                    'visit_matter_id': 'visit-matter-new',
                    'title': '新的就诊事项',
                    'visit_stage': 'pre_visit',
                    'patient_display_name': '演示患者',
                    'participant_display_name': '患者本人',
                }
            ),
        )

    def fulfill_history(route: Route) -> None:
        route.fulfill(
            status=200,
            content_type='application/json',
            body=json.dumps(
                {
                    'visit_matter_id': 'visit-matter-demo',
                    'messages': [
                        {
                            'id': 'message-restored-user',
                            'role': 'user',
                            'state': 'completed',
                            'parts': [{'type': 'text', 'text': '请整理就诊准备'}],
                            'created_at': '2030-01-01T00:00:00Z',
                            'updated_at': '2030-01-01T00:00:00Z',
                        },
                        {
                            'id': 'message-restored-assistant',
                            'role': 'assistant',
                            'state': 'completed',
                            'parts': [{'type': 'text', 'text': markdown_answer}],
                            'created_at': '2030-01-01T00:00:01Z',
                            'updated_at': '2030-01-01T00:00:01Z',
                        },
                    ],
                }
            ),
        )

    def fulfill_new_visit_history(route: Route) -> None:
        route.fulfill(
            status=200,
            content_type='application/json',
            body=json.dumps(
                {
                    'visit_matter_id': 'visit-matter-new',
                    'messages': [],
                }
            ),
        )

    def fulfill_archive(route: Route) -> None:
        if archive_conflict:
            route.fulfill(
                status=409,
                content_type='application/json',
                body=json.dumps(
                    {
                        'detail': {
                            'code': 'visit_matter_busy',
                            'message': '就诊事项仍有待确认操作',
                        }
                    }
                ),
            )
            return
        visit_matter_id = route.request.url.rsplit('/', 2)[-2]
        active_visit_matter_ids.remove(visit_matter_id)
        archived_visit_matter_ids.append(visit_matter_id)
        title = visit_titles[visit_matter_id]
        route.fulfill(
            status=200,
            content_type='application/json',
            body=json.dumps(
                {
                    'visit_matter_id': visit_matter_id,
                    'title': title,
                    'visit_stage': 'pre_visit',
                    'patient_display_name': '演示患者',
                    'participant_display_name': '患者本人',
                    'archived_at': '2030-01-01T00:00:00Z',
                }
            ),
        )

    def fulfill_archived_list(route: Route) -> None:
        route.fulfill(
            status=200,
            content_type='application/json',
            body=json.dumps(
                {
                    'visit_matters': [
                        {
                            'visit_matter_id': visit_matter_id,
                            'title': visit_titles[visit_matter_id],
                            'visit_stage': 'pre_visit',
                            'patient_display_name': '演示患者',
                            'participant_display_name': '患者本人',
                            'archived_at': '2030-01-01T00:00:00Z',
                        }
                        for visit_matter_id in archived_visit_matter_ids
                    ]
                }
            ),
        )

    def fulfill_active_list(route: Route) -> None:
        route.fulfill(
            status=200,
            content_type='application/json',
            body=json.dumps(
                {
                    'visit_matters': [
                        {
                            'visit_matter_id': visit_matter_id,
                            'title': visit_titles[visit_matter_id],
                            'visit_stage': 'pre_visit',
                            'patient_display_name': '演示患者',
                            'participant_display_name': '患者本人',
                            'archived_at': None,
                        }
                        for visit_matter_id in active_visit_matter_ids
                    ]
                }
            ),
        )

    def fulfill_rename(route: Route) -> None:
        visit_titles['visit-matter-demo'] = '初诊记录'
        route.fulfill(
            status=200,
            content_type='application/json',
            body=json.dumps(
                {
                    'visit_matter_id': 'visit-matter-demo',
                    'title': '初诊记录',
                    'visit_stage': 'pre_visit',
                    'patient_display_name': '演示患者',
                    'participant_display_name': '患者本人',
                    'archived_at': '2030-01-01T00:00:00Z',
                }
            ),
        )

    def fulfill_restore(route: Route) -> None:
        archived_visit_matter_ids.remove('visit-matter-demo')
        active_visit_matter_ids.append('visit-matter-demo')
        route.fulfill(
            status=200,
            content_type='application/json',
            body=json.dumps(
                {
                    'visit_matter_id': 'visit-matter-demo',
                    'title': '初诊记录',
                    'visit_stage': 'pre_visit',
                    'patient_display_name': '演示患者',
                    'participant_display_name': '患者本人',
                    'archived_at': None,
                }
            ),
        )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.route('**/v1/chat/turns', fulfill_chat)
        page.route('**/v1/visit-matters/visit-matter-demo/messages*', fulfill_history)
        page.route('**/v1/visit-matters/visit-matter-new/messages*', fulfill_new_visit_history)
        page.route('**/v1/visit-matters/*/archive', fulfill_archive)
        page.route('**/v1/visit-matters/visit-matter-demo/title', fulfill_rename)
        page.route('**/v1/visit-matters/visit-matter-demo/restore', fulfill_restore)
        page.route(
            '**/v1/visit-matters?participant_id=participant-demo&archived=true',
            fulfill_archived_list,
        )
        page.route('**/v1/visit-matters', fulfill_visit_matter)
        page.route(
            '**/v1/visit-matters?participant_id=participant-demo',
            fulfill_active_list,
        )
        page.on(
            'console',
            lambda message: browser_errors.append(message.text)
            if message.type == 'error'
            else None,
        )

        deadline = time.monotonic() + 30
        while True:
            try:
                page.goto(web_url, wait_until='domcontentloaded', timeout=60_000)
                break
            except Error:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.5)
        composer = page.get_by_role('textbox', name='输入就诊需求')
        composer.wait_for()
        page.get_by_text('医院数据', exact=True).wait_for()
        page.locator('.context-row').filter(has_text='医院数据').wait_for()

        composer.fill('请帮我整理就诊准备')
        page.get_by_role('button', name='发送消息').click()
        page.get_by_role('heading', name='就诊准备', level=2).wait_for()
        page.get_by_text('记录症状出现时间', exact=True).wait_for()
        page.get_by_text('MEDIPET_API_URL', exact=True).wait_for()
        assert len(seen_visit_matter_ids) == 1
        initial_visit_matter_id = seen_visit_matter_ids[0]
        active_visit_matter_ids.append(initial_visit_matter_id)

        composer.fill('**需要重点说明**\n\n- 夜间更明显')
        page.get_by_role('button', name='打开 Markdown 预览').click()
        page.locator('.composer-preview strong').get_by_text('需要重点说明').wait_for()
        page.get_by_role('button', name='关闭 Markdown 预览').click()

        page.get_by_role('button', name='新建就诊事项').click()
        created_visit_matter = page.locator('.visit-entry.active').filter(
            has_text='新的就诊事项',
        )
        created_visit_matter.wait_for()
        composer.fill('新的事项')
        page.get_by_role('button', name='发送消息').click()
        page.get_by_text('这是新的独立就诊事项。', exact=True).wait_for()
        assert seen_visit_matter_ids == [initial_visit_matter_id, 'visit-matter-new']

        page.get_by_role('button', name='演示患者 · 初次咨询 诊前准备').click()
        page.get_by_text('请整理就诊准备', exact=True).wait_for()
        page.get_by_role('heading', name='就诊准备', level=2).wait_for()
        active_visit_matter_ids[:] = ['visit-matter-demo', 'visit-matter-new']

        composer.fill('再次发送')
        page.get_by_role('button', name='发送消息').click()
        page.get_by_text('连接暂时中断，请检查后端服务后重试。', exact=True).wait_for()
        assert '模型服务暂时不可用' not in page.locator('body').inner_text()

        while page.locator('.visit-entry').count() > 0:
            previous_count = page.locator('.visit-entry').count()
            page.locator('.visit-entry.active').get_by_title('归档').click()
            page.wait_for_function(
                '(count) => document.querySelectorAll(".visit-entry").length < count',
                arg=previous_count,
            )
        page.get_by_role('heading', name='还没有历史记录', level=1).wait_for()

        page.get_by_role('button', name='查看已归档').click()
        page.get_by_role('heading', name='初次咨询', level=2).wait_for()
        page.locator('.visit-entry.active').get_by_title('重命名').click()
        page.get_by_role('textbox', name='新的历史记录名称').fill('初诊记录')
        page.get_by_role('button', name='保存名称').click()
        page.locator('.visit-entry.active').get_by_title('恢复').click()
        page.get_by_role('heading', name='初诊记录', level=2).wait_for()
        page.locator('.visit-entry.active').hover()
        page.screenshot(path=str(history_screenshot), full_page=True)

        page.screenshot(path=str(screenshot), full_page=True)
        assert not browser_errors, browser_errors

        page.set_viewport_size({'width': 390, 'height': 844})
        page.get_by_role('combobox', name='切换就诊事项').wait_for()
        page.get_by_role('button', name='在移动端新建就诊事项').wait_for()
        page.get_by_role('button', name='移动端查看已归档').wait_for()
        page.get_by_role('button', name='移动端重命名 初诊记录').wait_for()
        page.get_by_role('button', name='移动端归档 初诊记录').wait_for()
        archive_conflict = True
        page.get_by_role('button', name='移动端归档 初诊记录').click()
        page.get_by_role('alert').get_by_text('就诊事项仍有待确认操作', exact=True).wait_for()
        browser.close()

    print(f'MediPet browser smoke passed; screenshot={screenshot}')


if __name__ == '__main__':
    main()

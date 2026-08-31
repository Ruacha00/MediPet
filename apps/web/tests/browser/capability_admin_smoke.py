import os
import re
import time
from pathlib import Path

from playwright.sync_api import Error, Page, sync_playwright


def _open_when_ready(page: Page, url: str) -> None:
    deadline = time.monotonic() + 45
    while True:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            return
        except Error:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)


def _assert_desktop_layout(page: Page) -> None:
    dimensions = page.evaluate(
        """() => ({
          body: document.body.scrollWidth,
          shell: document.querySelector('.capability-admin-shell')?.scrollWidth,
          viewport: window.innerWidth,
          scrollRegion: document.querySelector('.admin-scroll-region')?.scrollHeight,
          scrollClient: document.querySelector('.admin-scroll-region')?.clientHeight,
        })"""
    )
    assert dimensions["body"] == dimensions["viewport"], dimensions
    assert dimensions["shell"] == dimensions["viewport"], dimensions
    assert dimensions["scrollRegion"] >= dimensions["scrollClient"], dimensions


def main() -> None:
    browser_errors: list[str] = []
    web_url = os.getenv("MEDIPET_WEB_URL", "http://localhost:3000")
    screenshot = Path(__file__).with_name("capability-admin-smoke.png")
    unique = str(int(time.time() * 1000))
    slug = f"governance-smoke-{unique}"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.on(
            "console",
            lambda message: browser_errors.append(message.text)
            if message.type == "error"
            else None,
        )

        _open_when_ready(page, f"{web_url}/admin/capabilities")
        page.get_by_role("heading", name="Skills", exact=True).wait_for()
        _assert_desktop_layout(page)

        page.get_by_role("link", name="新建 Skill").click()
        page.get_by_role("textbox", name="Slug").fill(slug)
        page.get_by_role("textbox", name="名称").fill("治理台烟测 Skill")
        page.get_by_role("textbox", name="描述").fill("验证能力治理闭环")
        page.get_by_role("combobox", name="Skill 类型").select_option("tool-assisted")
        page.get_by_role("textbox", name="变更说明").fill("Compose browser smoke")
        page.get_by_role("textbox", name="Instructions Markdown").fill(
            "# 治理验证\n\n仅使用已绑定的查询 Tool。"
        )
        page.get_by_role("button", name="创建草稿").click()
        page.get_by_text(slug, exact=True).wait_for()
        skill_id = page.url.rstrip("/").rsplit("/", 1)[-1]
        _assert_desktop_layout(page)

        page.get_by_role("button", name="Tool 绑定 0").click()
        tool_selector = page.get_by_label("选择已部署 Tool 版本")
        options = tool_selector.locator("option").all_text_contents()
        preferred = next(
            (label for label in options if "hospital.search_slots@" in label and "已启用" in label),
            None,
        )
        if preferred is None:
            preferred = next(
                label for label in options if " · read · " not in label and "已启用" in label
            ) if any("已启用" in label for label in options) else None
        assert preferred is not None, "Browser smoke requires an available, enabled Tool"
        tool_selector.select_option(label=preferred)
        page.get_by_role("button", name="绑定 Tool").click()
        page.get_by_text("Tool 已绑定", exact=True).wait_for()

        page.get_by_role("button", name="提交审核").click()
        page.get_by_role("button", name=re.compile(r"^发布 v")).wait_for()
        page.get_by_role("button", name=re.compile(r"^发布 v")).click()
        page.get_by_role("alertdialog", name="确认发布 Skill").get_by_role(
            "button", name="确认发布"
        ).click()
        page.get_by_text("治理状态已更新", exact=True).wait_for()

        page.get_by_role("link", name="Tools").click()
        page.get_by_role("heading", name="Tools", exact=True).wait_for()
        active_tool = preferred.split(" · ")[1].split("@")[0]
        page.get_by_role("searchbox", name="搜索 Tools").fill(active_tool)
        page.get_by_role("link", name=f"查看 Tool {active_tool}").click()
        _assert_desktop_layout(page)
        page.get_by_role("button", name=re.compile(r"^停用 v")).click()
        page.get_by_role("alertdialog", name="确认停用 Tool").get_by_role(
            "button", name="确认停用"
        ).click()
        page.get_by_text("Tool 治理状态已更新", exact=True).wait_for()
        page.get_by_role("button", name=re.compile(r"^启用 v")).click()
        page.get_by_role("alertdialog", name="确认启用 Tool").get_by_role(
            "button", name="确认启用"
        ).click()
        page.get_by_text("Tool 治理状态已更新", exact=True).wait_for()

        page.get_by_role("link", name="变更记录").click()
        page.get_by_role("heading", name="变更记录").wait_for()
        _assert_desktop_layout(page)
        page.get_by_label("对象 ID").fill(skill_id)
        page.locator(".admin-audit-ledger strong").get_by_text("发布", exact=True).wait_for()
        page.reload(wait_until="domcontentloaded")
        page.get_by_role("heading", name="变更记录").wait_for()
        page.get_by_role("link", name="Skills").click()
        page.get_by_role("searchbox", name="搜索 Skills").fill(slug)
        page.get_by_role("link", name="查看 Skill 治理台烟测 Skill").wait_for()

        page.set_viewport_size({"width": 1440, "height": 900})
        for path in (
            "/admin/capabilities/skills",
            "/admin/capabilities/skills/new",
            f"/admin/capabilities/skills/{skill_id}",
            "/admin/capabilities/tools",
            f"/admin/capabilities/tools/{active_tool}",
            "/admin/capabilities/audits",
        ):
            page.goto(f"{web_url}{path}", wait_until="domcontentloaded")
            page.locator(".capability-admin-shell").wait_for()
            _assert_desktop_layout(page)
        page.goto(f"{web_url}/admin/capabilities/skills", wait_until="domcontentloaded")
        page.get_by_role("searchbox", name="搜索 Skills").fill(slug)
        page.screenshot(path=str(screenshot), full_page=True)
        assert not browser_errors, browser_errors
        browser.close()

    print(f"MediPet capability admin smoke passed; screenshot={screenshot}")


if __name__ == "__main__":
    main()

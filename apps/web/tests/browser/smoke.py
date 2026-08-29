from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    browser_errors: list[str] = []
    screenshot = Path(__file__).with_name("medipet-smoke.png")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on(
            "console",
            lambda message: browser_errors.append(message.text)
            if message.type == "error"
            else None,
        )

        page.goto("http://localhost:3000")
        page.wait_for_load_state("networkidle")
        page.get_by_role("heading", name="把复杂的门诊流程，变成一次从容的对话。").wait_for()

        page.get_by_text('医院数据', exact=True).wait_for()
        page.get_by_text('未配置', exact=True).wait_for()
        page.get_by_role('button', name='整理症状').wait_for()

        page.screenshot(path=str(screenshot), full_page=True)
        assert not browser_errors, browser_errors
        browser.close()

    print(f"MediPet browser smoke passed; screenshot={screenshot}")


if __name__ == "__main__":
    main()

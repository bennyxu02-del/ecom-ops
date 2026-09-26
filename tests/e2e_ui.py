"""界面端到端走查（React 版）：按演示路线点一遍并截图。

用法：先启动服务（默认 http://127.0.0.1:8000），再运行
    python tests/e2e_ui.py [BASE_URL] [截图目录]
需要：pip install playwright && playwright install chromium
"""
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

B = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "e2e_shots")
OUT.mkdir(parents=True, exist_ok=True)


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--no-proxy-server"])
        pg = await b.new_page(viewport={"width": 1440, "height": 900})
        pg.set_default_timeout(20000)
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)

        async def shot(name):
            await pg.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
            print("  截图", name, flush=True)

        print("1 经营总览", flush=True)
        await pg.goto(B + "/#/overview")
        await pg.wait_for_selector(".kpi")
        await pg.wait_for_timeout(800)
        await shot("01_overview")

        print("2 商品列表：排序 + 筛选", flush=True)
        await pg.goto(B + "/#/products")
        await pg.wait_for_selector(".ant-table-row")
        await pg.get_by_role("columnheader", name="健康度").click()   # 按健康度排序
        await pg.wait_for_timeout(400)
        await shot("02_products")
        await pg.get_by_text("全部商品").click()
        await pg.wait_for_timeout(800)
        n_all = await pg.locator(".ant-table-row").count()
        print("  全部商品行数", n_all)

        print("3 从待办进入 P01 诊断", flush=True)
        await pg.goto(B + "/#/overview")
        await pg.wait_for_selector(".alert-card")
        await pg.click(".alert-card >> nth=0")
        await pg.wait_for_selector(".plan", timeout=30000)
        await pg.wait_for_timeout(600)
        await pg.locator(".ant-segmented-item", has_text="近 90 天").first.click()      # 图表时间范围切换
        await pg.wait_for_timeout(400)
        await shot("03_product_diag")

        print("4 采纳 → 标记已执行 → 驳回", flush=True)
        await pg.locator(".plan .pf button", has_text="采纳").first.click()
        await pg.wait_for_selector(".plan .pf button:has-text('标记已执行')")
        await pg.locator(".plan .pf button", has_text="标记已执行").first.click()
        await pg.wait_for_timeout(800)
        rej = pg.locator(".plan .pf button", has_text="驳回")
        if await rej.count():
            await rej.first.click()
            await pg.get_by_role("button", name="确认驳回").click()
            await pg.wait_for_timeout(800)

        print("5 追问", flush=True)
        await pg.get_by_placeholder("例如：如果明天补上货").fill("如果明天补上货，大概能恢复多少？")
        await pg.get_by_role("button", name="发送").click()
        await pg.wait_for_function("document.querySelectorAll('.chat .m.a .md').length > 0", timeout=30000)
        await pg.wait_for_timeout(1500)
        await shot("04_product_actions_chat")

        print("6 预警中心：忽略一张", flush=True)
        await pg.goto(B + "/#/alerts")
        await pg.wait_for_selector(".alert-card")
        await shot("05_alerts")
        await pg.locator(".alert-card button", has_text="忽略").last.click()
        await pg.get_by_role("button", name="确认忽略").click()
        await pg.wait_for_timeout(800)

        print("7 行动跟踪", flush=True)
        await pg.goto(B + "/#/actions")
        await pg.wait_for_selector(".ant-table")
        await pg.wait_for_timeout(500)
        await shot("06_actions")

        print("8 生成周报并发布", flush=True)
        await pg.goto(B + "/#/reports")
        await pg.get_by_role("button", name="生成本周周报").click()
        await pg.wait_for_function("location.hash.startsWith('#/report/')", timeout=60000)
        await pg.wait_for_selector(".md h1, .md h2")
        await pg.get_by_role("button", name="确认发布").click()
        await pg.wait_for_timeout(600)
        await shot("07_report")

        print("9 切换零食数据集，诊断 S01", flush=True)
        await pg.goto(B + "/#/overview")
        await pg.click(".topbar .ant-select")
        await pg.get_by_title("数据集：休闲零食").click()
        await pg.wait_for_timeout(1200)
        await pg.goto(B + "/#/product/S01?diag=1")
        await pg.wait_for_selector(".plan", timeout=30000)
        await pg.wait_for_timeout(500)
        await shot("08_snacks_diag")

        print("10 方法库", flush=True)
        await pg.goto(B + "/#/methods")
        await pg.get_by_role("tab", name="动作库").click()
        await pg.wait_for_timeout(500)
        await shot("09_methods")

        # 切回 3C，避免影响下次打开
        await pg.evaluate("localStorage.setItem('ds','3c')")
        print("控制台错误：", errs or "无")
        await b.close()
        return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

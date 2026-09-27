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

        async def save_todo():
            """新建待办确认窗：绑定了飞书显示「保存并通知 N 人」，否则「保存」"""
            await pg.wait_for_selector(".ant-modal .todo-steps")
            await shot("03b_todo_confirm")
            await pg.locator(".ant-modal-footer .ant-btn-primary").click()
            await pg.wait_for_selector(".ant-modal .todo-steps", state="detached")

        print("4 采纳（确认窗）→ 驳回另一个方案", flush=True)
        await pg.locator(".plan .pf button", has_text="采纳").first.click()
        await save_todo()
        await pg.wait_for_selector(".plan .pf:has-text('已加入待办')")
        rej = pg.locator(".plan .pf button", has_text="驳回")
        if await rej.count():
            await rej.first.click()
            await pg.get_by_role("button", name="确认驳回").click()
            await pg.wait_for_selector(".plan.rejected")

        print("5 追问", flush=True)
        await pg.get_by_placeholder("例如：如果明天补上货").fill("如果明天补上货，大概能恢复多少？")
        await pg.locator(".chat .ant-btn-primary").click()
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

        print("7 待办中心：我要做的 / 全部待办", flush=True)
        await pg.goto(B + "/#/actions")
        await pg.wait_for_selector(".view-tabs")
        await pg.wait_for_timeout(500)
        await shot("06_actions_mine")
        await pg.locator(".view-tabs .ant-segmented-item", has_text="全部待办").click()
        await pg.wait_for_selector(".ant-table")
        await shot("06_actions_all")

        print("7a 复盘预置的 P05", flush=True)
        await pg.locator(".view-tabs .ant-segmented-item", has_text="我要做的").click()
        await pg.locator(".ant-card", has_text="待复盘").get_by_role("button", name="去复盘").click()
        await pg.wait_for_selector(".stage-box.review")
        await shot("06a_review")
        await pg.get_by_role("button", name="确认复盘").click()
        await pg.wait_for_selector(".ant-drawer .ant-steps-item-finish >> nth=3")
        await pg.locator(".ant-drawer-close").click()
        await pg.wait_for_timeout(400)

        print("7b 新建待办 → 详情抽屉编辑", flush=True)
        await pg.get_by_role("button", name="新建待办").click()
        await pg.wait_for_selector(".ant-modal .todo-steps")
        await pg.locator(".ant-modal .ant-select").first.click()
        await pg.keyboard.type("磁吸")
        await pg.locator(".ant-select-item", has_text="磁吸无线充电宝").click()
        await pg.get_by_placeholder("例如：确认白色款到货").fill("准备达人二次投放素材")
        await pg.locator(".ant-modal .todo-steps input.ant-input").first.fill("整理达人视频的高转化片段")
        await pg.get_by_role("button", name="添加步骤").click()
        await pg.locator(".ant-modal .todo-steps input.ant-input").nth(1).fill("追加站外投放预算 2 天")
        await pg.locator(".ant-modal .todo-steps .ts").nth(1).locator(".ant-select").click()
        await pg.locator(".ant-select-dropdown:visible .ant-select-item", has_text="投放运营").click()
        await shot("06b_todo_new")
        await save_todo()
        await pg.wait_for_selector(".ant-drawer-content")
        await pg.locator(".ant-drawer textarea").fill("素材由品牌部提供")
        await pg.get_by_role("button", name="保存备注").click()
        await pg.wait_for_selector(".ant-drawer .ant-timeline-item:has-text('更新备注')")
        await shot("06c_todo_drawer")
        await pg.locator(".ant-drawer-close").click()
        await pg.wait_for_timeout(400)

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

        print("9b 协同：采纳带同事步骤的方案 → 发送 → 同事处理 → 我勾完 → 进入跟踪", flush=True)
        await pg.locator(".plan", has_text="差评处理").locator(".pf button", has_text="采纳").first.click()
        await save_todo()
        await pg.goto(B + "/#/actions?view=mine")
        await pg.wait_for_selector(".view-tabs")
        await pg.locator(".ant-card", has_text="需要我处理的协同").get_by_role("button", name="发送").first.click()
        await pg.wait_for_selector(".ant-modal textarea")
        await shot("08b_handoff_send")
        await pg.get_by_role("button", name="复制文字，标记已通知").click()
        await pg.wait_for_timeout(800)
        hid = await pg.evaluate("fetch('/api/handoffs?ds=snacks').then(r=>r.json()).then(x=>x[0].id)")
        await pg.goto(B + f"/#/h/{hid}")
        await pg.wait_for_selector(".hv-msg")
        await pg.get_by_placeholder("处理说明").fill("批次 B0912 已隔离，其余批次抽检正常")
        await pg.get_by_role("button", name="已完成").click()
        await pg.wait_for_selector(".verify.ok")
        await shot("08c_handoff_view")
        await pg.goto(B + "/#/actions?view=mine")
        await pg.wait_for_selector(".view-tabs")
        await pg.wait_for_timeout(600)
        boxes = pg.locator(".ant-card", has_text="我的步骤").locator(".ant-checkbox-input")
        while await boxes.count():
            await boxes.first.click()
            await pg.wait_for_timeout(700)
        await shot("08d_mine_done")
        await pg.goto(B + "/#/collab")                      # 旧链接跳到待办中心「等同事」筛选
        await pg.wait_for_selector(".filter-chips")
        await pg.locator(".filter-chips .ant-tag-checkable", has_text="跟踪中").click()
        await pg.wait_for_selector(".ant-table-row:has-text('差评处理')")
        await shot("08e_actions_snacks")

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

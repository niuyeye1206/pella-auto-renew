#!/usr/bin/env python3
"""Pella.app 免费服务器自动续期。

流程：登录面板 -> 打开服务器详情页 -> 领取所有可用的 "Claim 16 Hours" 链接
（每条 +16 小时，每天刷新两条）-> 读新的剩余有效期 -> TG 通知。
环境变量：PELLA_EMAIL / PELLA_PASSWORD / TG_BOT_TOKEN / TG_CHAT_ID / SERVER_URL
"""
import asyncio
import os
import re
import sys

import requests

PELLA_EMAIL = os.environ.get("PELLA_EMAIL", "")
PELLA_PASSWORD = os.environ.get("PELLA_PASSWORD", "")
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
SERVER_URL = os.environ.get(
    "SERVER_URL", "https://www.pella.app/server/82b2b0c4d8574074baec5809a20301b8"
)
LOGIN_URL = "https://www.pella.app/login"


def tg_send(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("TG 未配置，跳过通知")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": text},
            timeout=20,
        )
        print("TG 通知状态:", r.status_code, r.text[:200])
    except Exception as e:
        print("TG 通知失败:", e)


JS_FIND_EMAIL = """() => {
    const cands = [
        "input[placeholder*='email' i]",
        "input[placeholder*='Email']",
        "input[name='identifier']",
        "input[name='email']",
        "input[type='email']",
        "input[aria-label*='Email' i]",
    ];
    for (const sel of cands) {
        const el = document.querySelector(sel);
        if (el && el.offsetParent !== null) return el;
    }
    return null;
}"""

JS_FIND_PASSWORD = """() => {
    const el = document.querySelector("input[type='password']");
    if (el && el.offsetParent !== null) return el;
    return null;
}"""

JS_FIND_BUTTON = """(label) => {
    const btns = [...document.querySelectorAll("button")].filter(b => b.offsetParent !== null);
    const exact = btns.find(b => b.textContent.trim() === label);
    if (exact) return exact;
    const starts = btns.find(b => {
        const t = b.textContent.trim();
        return t.startsWith(label) && !t.includes("with Google");
    });
    return starts || null;
}"""

JS_EXPIRY = """() => {
    const m = document.body.innerText.match(/Your server expires in[^\\n]*/i);
    return m ? m[0].trim() : "";
}"""

JS_CLAIM_LINKS = """() => {
    const links = [...document.querySelectorAll("a")].filter(a =>
        a.offsetParent !== null && a.textContent.includes("Claim 16 Hours")
    );
    return links.map(a => a.getAttribute("href")).filter(Boolean);
}"""


async def find_handle(page, js, arg=None):
    """用 JS 找元素，返回 ElementHandle 或 None。"""
    handle = await page.evaluateHandle(js, arg) if arg is not None else await page.evaluateHandle(js)
    element = handle.asElement()
    if element is None:
        await handle.dispose()
        return None
    return element


async def wait_for(page, js, timeout=30000, poll=1000):
    """轮询 JS 直到返回真值，返回该值；超时返回 None。"""
    waited = 0
    while waited < timeout:
        val = await page.evaluate(js)
        if val:
            return val
        await asyncio.sleep(poll / 1000)
        waited += poll
    return None


JS_FIELD_LEN = """(kind) => {
    if (kind === 'email') {
        const cands = [...document.querySelectorAll('input')].filter(i => i.offsetParent !== null);
        const el = cands.find(i =>
            (i.placeholder && /email/i.test(i.placeholder)) ||
            (i.name && /identifier|email/i.test(i.name)) ||
            (i.type === 'email')
        );
        return el ? el.value.length : -1;
    }
    const el = [...document.querySelectorAll("input[type='password']")].find(i => i.offsetParent !== null);
    return el ? el.value.length : -1;
}"""


async def type_robust(page, kind, value):
    """点进输入框后用真实键盘事件逐字输入（确保 React 状态同步），
    填完核对字符数，丢字符就清空重输，最多 3 次。返回最终字符数。"""
    find_js = JS_FIND_EMAIL if kind == "email" else JS_FIND_PASSWORD
    length = -1
    for attempt in range(3):
        el = await find_handle(page, find_js)
        if not el:
            return -1
        await el.click()
        await asyncio.sleep(0.3)
        await page.keyboard.down("Control")
        await page.keyboard.press("KeyA")
        await page.keyboard.up("Control")
        await page.keyboard.press("Backspace")
        await page.keyboard.type(value, {"delay": 50})
        await asyncio.sleep(0.5)
        length = await page.evaluate(JS_FIELD_LEN, kind)
        print(f"{kind} 填入后字符数: {length} 期望: {len(value)} (尝试 {attempt + 1})")
        if length == len(value):
            return length
    return length


async def main():
    from pyppeteer import launch

    if not PELLA_EMAIL or not PELLA_PASSWORD:
        print("缺少 PELLA_EMAIL 或 PELLA_PASSWORD")
        sys.exit(2)

    browser = await launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--window-size=1280,900",
        ],
    )
    page = await browser.newPage()
    await page.setViewport({"width": 1280, "height": 900})
    claimed = 0
    expiry_before = ""
    expiry_after = ""
    try:
        # ---- 登录（Clerk 多步：邮箱 -> Continue -> 密码 -> Continue/Sign in）----
        print("打开登录页", LOGIN_URL)
        await page.goto(LOGIN_URL, {"waitUntil": "networkidle2", "timeout": 60000})
        await asyncio.sleep(2)
        email_len = await type_robust(page, "email", PELLA_EMAIL)
        if email_len < 0:
            print("找不到邮箱输入框")
            await page.screenshot({"path": "login_fail.png"})
            tg_send("Pella 续期失败：登录页找不到邮箱输入框（页面可能变了）。")
            sys.exit(1)
        if email_len != len(PELLA_EMAIL):
            print("邮箱未完整填入")
            await page.screenshot({"path": "login_fail.png"})
            tg_send("Pella 续期失败：邮箱框未完整填入。")
            sys.exit(1)
        cont = await find_handle(page, JS_FIND_BUTTON, "Continue")
        if not cont:
            print("找不到 Continue 按钮")
            sys.exit(1)
        await cont.click()
        print("已点 Continue，等密码框...")
        pwd_ready = await wait_for(
            page,
            "() => { const el = document.querySelector(\"input[type='password']\");"
            " return !!(el && el.offsetParent !== null); }",
            timeout=30000,
        )
        if not pwd_ready:
            print("点 Continue 后 30 秒未出现密码框")
            await page.screenshot({"path": "login_fail.png"})
            tg_send("Pella 续期失败：点 Continue 后未出现密码框。")
            sys.exit(1)
        pwd_len = await type_robust(page, "password", PELLA_PASSWORD)
        if pwd_len != len(PELLA_PASSWORD):
            print("密码未完整填入")
            await page.screenshot({"path": "login_fail.png"})
            tg_send("Pella 续期失败：密码框未完整填入。")
            sys.exit(1)
        submit = await find_handle(page, JS_FIND_BUTTON, "Continue")
        if not submit:
            submit = await find_handle(page, JS_FIND_BUTTON, "Sign in")
        if not submit:
            print("找不到提交按钮")
            sys.exit(1)
        await submit.click()
        print("已提交登录，等跳转...")
        for _ in range(30):
            url = page.url
            if "/login" not in url:
                break
            await asyncio.sleep(1)
        if "/login" in page.url:
            print("登录后仍停留在登录页，登录失败，当前 URL:", page.url)
            err_text = await page.evaluate(
                "() => document.body.innerText.slice(0, 800)"
            )
            print("登录页可见文字:", err_text)
            await page.screenshot({"path": "login_fail.png"})
            tg_send("Pella 续期失败：登录未成功（仍停留在登录页），请检查账号密码。")
            sys.exit(1)
        print("登录成功，当前 URL:", page.url)

        # ---- 服务器详情页：读剩余时间，找可领取的链接 ----
        print("打开服务器页", SERVER_URL)
        await page.goto(SERVER_URL, {"waitUntil": "networkidle2", "timeout": 60000})
        await asyncio.sleep(3)
        expiry_before = await page.evaluate(JS_EXPIRY) or ""
        print("领取前剩余时间:", expiry_before)
        links = await page.evaluate(JS_CLAIM_LINKS)
        print("找到可领取的链接:", len(links), links)
        if not links:
            msg = (
                "Pella 续期检查：暂无可领取的链接（两条都已领过或未刷新）\n"
                f"剩余时间：{expiry_before or '未读到'}\n"
                "服务器 pella-node 仍在运行，下次定时再试。"
            )
            print(msg)
            tg_send(msg)
            return

        # ---- 逐条领取 ----
        for href in links:
            url = href if href.startswith("http") else "https://www.pella.app" + href
            print("领取:", url)
            await page.goto(url, {"waitUntil": "networkidle2", "timeout": 60000})
            ok = await wait_for(
                page,
                "() => document.body.innerText.includes('Server renewed succesfully')",
                timeout=30000,
            )
            if ok:
                claimed += 1
                print("领取成功 +16h")
            else:
                txt = (await page.evaluate("() => document.body.innerText"))[:300]
                print("领取未确认成功，页面文字:", txt)

        # ---- 回详情页读新的剩余时间 ----
        await page.goto(SERVER_URL, {"waitUntil": "networkidle2", "timeout": 60000})
        await asyncio.sleep(3)
        expiry_after = await page.evaluate(JS_EXPIRY) or ""
        print("领取后剩余时间:", expiry_after)
        msg = (
            f"Pella 续期成功：领取 {claimed} 条链接（每条 +16 小时）\n"
            f"剩余时间：{expiry_before or '未读到'} -> {expiry_after or '未读到'}\n"
            "服务器 pella-node 仍在运行。"
        )
        print(msg)
        tg_send(msg)
    finally:
        await browser.close()


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())

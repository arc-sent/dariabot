import asyncio
import json
import logging
import os
import random
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from dotenv import load_dotenv

BASE = Path(__file__).parent
load_dotenv(BASE / ".env")

TOKEN = os.getenv("BOT_TOKEN", "").strip()
ALLOWED = {
    int(x) for x in os.getenv("ALLOWED_USER_IDS", "").replace(" ", "").split(",") if x
}
GIRLFRIEND_ID = int(os.getenv("GIRLFRIEND_ID", "0") or 0)
MENU_BTN = "меню"
STATE_FILE = Path(os.getenv("STATE_FILE") or BASE / "state.json")


def load_content() -> dict:
    path = BASE / "messages.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"Ошибка в messages.json: строка {e.lineno}, символ {e.colno}: {e.msg}")
    cats = data.get("categories")
    if not cats:
        raise SystemExit("В messages.json нет категорий.")
    seen = set()
    for c in cats:
        for key in ("id", "title", "messages"):
            if key not in c:
                raise SystemExit(f"У категории {c} нет поля '{key}'.")
        if c["id"] in seen:
            raise SystemExit(f"Повторяющийся id: {c['id']}")
        seen.add(c["id"])
        if not c["messages"]:
            raise SystemExit(f"В категории '{c['title']}' нет сообщений.")
        d = c.get("date")
        if d:
            try:
                datetime.strptime(d, "%m-%d" if len(d) == 5 else "%Y-%m-%d")
            except ValueError:
                raise SystemExit(
                    f"У категории '{c['title']}' неверная дата '{d}'. Формат: ММ-ДД (каждый год) или ГГГГ-ММ-ДД (один раз)."
                )
    r = data.get("random", {})
    if r:
        pool = r.get("love", []) + r.get("miss", [])
        if not pool:
            raise SystemExit("В блоке random нет сообщений (love / miss).")
        for k in ("from", "to"):
            try:
                datetime.strptime(r.get(k, "10:00" if k == "from" else "22:00"), "%H:%M")
            except ValueError:
                raise SystemExit(f"В блоке random неверное время в поле '{k}'. Формат ЧЧ:ММ.")
    try:
        ZoneInfo(data.get("timezone", "Europe/Moscow"))
    except Exception:
        raise SystemExit(f"Неизвестный часовой пояс: {data.get('timezone')}")
    return data


CONTENT = load_content()
CATS = {c["id"]: c for c in CONTENT["categories"]}
TZ = ZoneInfo(CONTENT.get("timezone", "Europe/Moscow"))
RANDOM = CONTENT.get("random", {})
WELCOME = CONTENT.get("welcome", "выбери, что тебе сейчас нужно:")

# id категории -> перемешанная очередь ещё не показанных индексов (по каждому чату)
queues: dict[tuple[int, str], list[int]] = {}
last_shown: dict[tuple[int, str], int] = {}

dp = Dispatcher()


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state() -> None:
    STATE_FILE.write_text(json.dumps(STATE, ensure_ascii=False, indent=2), encoding="utf-8")


STATE = load_state()
STATE.setdefault("chats", [])
STATE.setdefault("sent", {})


def allowed(user_id: int) -> bool:
    return not ALLOWED or user_id in ALLOWED


def menu_kb() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=f"{c.get('emoji', '')} {c['title']}".strip(), callback_data=f"c:{c['id']}")
        for c in CONTENT["categories"]
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=MENU_BTN)]], resize_keyboard=True, is_persistent=True
    )


def pick(chat_id: int, cat_id: str) -> str:
    msgs = CATS[cat_id]["messages"]
    key = (chat_id, cat_id)
    q = queues.get(key)
    if not q:
        q = list(range(len(msgs)))
        random.shuffle(q)
        # не начинать новый круг с той же фразы, что показывали последней
        if len(q) > 1 and q[-1] == last_shown.get(key):
            q[0], q[-1] = q[-1], q[0]
        queues[key] = q
    idx = q.pop()
    last_shown[key] = idx
    return msgs[idx]


def message_kb(cat_id: str) -> InlineKeyboardMarkup:
    row = []
    if len(CATS[cat_id]["messages"]) > 1:
        row.append(InlineKeyboardButton(text="ещё одна", callback_data=f"c:{cat_id}"))
    row.append(InlineKeyboardButton(text="назад", callback_data="menu"))
    return InlineKeyboardMarkup(inline_keyboard=[row])


@dp.message(CommandStart())
@dp.message(F.text == MENU_BTN)
async def start(m: Message):
    if not allowed(m.from_user.id):
        return await m.answer("это личный бот")
    if m.chat.id not in STATE["chats"]:
        STATE["chats"].append(m.chat.id)
        save_state()
    await m.answer(WELCOME, reply_markup=reply_kb())
    await m.answer("выбирай", reply_markup=menu_kb())


@dp.callback_query(F.data == "menu")
async def back(c: CallbackQuery):
    if not allowed(c.from_user.id):
        return await c.answer()
    await c.message.edit_text(WELCOME, reply_markup=menu_kb())
    await c.answer()


@dp.callback_query(F.data.startswith("c:"))
async def category(c: CallbackQuery):
    if not allowed(c.from_user.id):
        return await c.answer()
    cat_id = c.data[2:]
    if cat_id not in CATS:
        await c.answer("этой кнопки больше нет", show_alert=True)
        return
    text = pick(c.message.chat.id, cat_id)
    await c.message.edit_text(text, reply_markup=message_kb(cat_id))
    await c.answer()


@dp.message()
async def fallback(m: Message):
    if not allowed(m.from_user.id):
        return
    await m.answer("я тебя обнимаю. вот что у меня есть:", reply_markup=menu_kb())


def recipients() -> list[int]:
    if GIRLFRIEND_ID:
        return [GIRLFRIEND_ID]
    return list(STATE["chats"])


async def push(bot: Bot, text: str) -> None:
    for chat_id in recipients():
        try:
            await bot.send_message(chat_id, text)
        except Exception:
            logging.exception("не удалось отправить в чат %s", chat_id)


def event_today(cat: dict, today: date) -> bool:
    d = cat.get("date")
    if not d:
        return False
    return today.strftime("%m-%d") == d if len(d) == 5 else today.isoformat() == d


def plan_random(today: str) -> dict:
    start = datetime.strptime(RANDOM.get("from", "10:00"), "%H:%M")
    end = datetime.strptime(RANDOM.get("to", "22:00"), "%H:%M")
    lo, hi = start.hour * 60 + start.minute, end.hour * 60 + end.minute
    n = min(int(RANDOM.get("per_day", 2)), max(hi - lo, 0))
    times = sorted(random.sample(range(lo, hi), n)) if n > 0 else []
    return {"date": today, "times": times, "done": 0}


async def scheduler(bot: Bot) -> None:
    while True:
        try:
            now = datetime.now(TZ)
            today = now.date().isoformat()

            # знаковые события: с 00:00 заданного дня
            for cat in CONTENT["categories"]:
                if event_today(cat, now.date()) and STATE["sent"].get(cat["id"]) != today:
                    STATE["sent"][cat["id"]] = today
                    save_state()
                    for chat_id in recipients():
                        text = pick(chat_id, cat["id"])
                        try:
                            await bot.send_message(chat_id, text)
                        except Exception:
                            logging.exception("не удалось отправить в чат %s", chat_id)

            # случайные признания в течение дня
            if RANDOM:
                plan = STATE.get("random_plan")
                if not plan or plan["date"] != today:
                    plan = STATE["random_plan"] = plan_random(today)
                    save_state()
                minutes = now.hour * 60 + now.minute
                pool = RANDOM.get("love", []) + RANDOM.get("miss", [])
                while plan["done"] < len(plan["times"]) and minutes >= plan["times"][plan["done"]]:
                    t = plan["times"][plan["done"]]
                    plan["done"] += 1
                    save_state()
                    if minutes - t <= 90:  # если бот был выключен давно, пропускаем
                        await push(bot, random.choice(pool))
        except Exception:
            logging.exception("ошибка планировщика")
        await asyncio.sleep(20)


async def main():
    if not TOKEN:
        raise SystemExit("Не задан BOT_TOKEN в .env")
    logging.basicConfig(level=logging.INFO)
    proxy = os.getenv("PROXY_URL", "").strip()
    session = AiohttpSession(proxy=proxy, timeout=60) if proxy else None
    bot = Bot(TOKEN, session=session)
    asyncio.create_task(scheduler(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

# -*- coding: utf-8 -*-
"""
💖✨ Bimbo Planner Bot ✨💖
Постоянно работающая версия (long polling) для Docker / VPS.
"""

import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta, date

import telebot
from telebot import types

# ====== НАСТРОЙКИ ======
TOKEN = os.environ.get("TOKEN", "")
MY_ID = int(os.environ.get("MY_ID", "0"))
TZ_OFFSET = int(os.environ.get("TZ_OFFSET", "3"))  # МСК

# Локально можно положить .env рядом со скриптом
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path) and not TOKEN:
    with open(_env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
    TOKEN = os.environ.get("TOKEN", "")
    MY_ID = int(os.environ.get("MY_ID", "0"))
    TZ_OFFSET = int(os.environ.get("TZ_OFFSET", "3"))

if not TOKEN or not MY_ID:
    print("ОШИБКА: укажите TOKEN и MY_ID в переменных окружения", file=sys.stderr)
    sys.exit(1)

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(HERE, "tasks.json")
STATE_FILE = os.path.join(HERE, "state.json")

bot = telebot.TeleBot(TOKEN)

WEEKDAYS = {
    "пн": 0, "пнд": 0, "понедельник": 0,
    "вт": 1, "втр": 1, "вторник": 1,
    "ср": 2, "срд": 2, "среда": 2, "среду": 2,
    "чт": 3, "чтв": 3, "четверг": 3,
    "пт": 4, "птн": 4, "пятница": 4, "пятницу": 4,
    "сб": 5, "сбт": 5, "суббота": 5, "субботу": 5,
    "вс": 6, "вск": 6, "воскресенье": 6,
}

DAY_PARTS = {
    "утром": 9, "утро": 9,
    "днём": 13, "днем": 13, "день": 13,
    "вечером": 19, "вечер": 19,
    "ночью": 22, "ночь": 22,
}

pending = {}


def now():
    return datetime.utcnow() + timedelta(hours=TZ_OFFSET)


def _load(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_tasks():
    return _load(DATA_FILE, [])


def save_tasks(tasks):
    _save(DATA_FILE, tasks)


def load_state():
    return _load(STATE_FILE, {"reminded_sunday": "", "reminded_daily": ""})


def save_state(state):
    _save(STATE_FILE, state)


def cleanup_old():
    tasks = load_tasks()
    cutoff = now() - timedelta(days=1)
    new_tasks = []
    for t in tasks:
        try:
            if datetime.fromisoformat(t["when"]) > cutoff:
                new_tasks.append(t)
        except Exception:
            pass
    if len(new_tasks) != len(tasks):
        save_tasks(new_tasks)


def parse_date(text):
    t = text.strip().lower()
    today = now().date()
    for word, delta in (("послезавтра", 2), ("завтра", 1), ("сегодня", 0)):
        if t.startswith(word):
            return today + timedelta(days=delta), text[len(word):].strip()
    for word, wd in WEEKDAYS.items():
        m = re.match(rf"^{word}\b", t)
        if m:
            cur_wd = today.weekday()
            days_ahead = (wd - cur_wd) % 7
            if days_ahead == 0:
                days_ahead = 7
            return today + timedelta(days=days_ahead), text[m.end():].strip()
    m = re.match(r"^(\d{1,2})[\.\-/](\d{1,2})(?:[\.\-/](\d{2,4}))?", t)
    if m:
        d, mo, y = m.group(1), m.group(2), m.group(3)
        y = int(y) if y else today.year
        if y < 100:
            y += 2000
        try:
            result = date(y, int(mo), int(d))
            if not m.group(3) and result < today:
                result = result.replace(year=y + 1)
            return result, text[m.end():].strip()
        except ValueError:
            pass
    return None, text


def parse_time(text):
    t = text.strip().lower()
    if t.startswith("в "):
        t = t[2:].strip()
        prefix = 2 + (len(text) - len(text.lstrip()))
    else:
        prefix = 0
    m = re.match(r"^(\d{1,2})[:\.](\d{2})", t)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h < 24 and 0 <= mi < 60:
            return (h, mi), text[prefix + m.end():].strip()
    m = re.match(r"^(\d{4})\b", t)
    if m:
        v = int(m.group(1))
        h, mi = v // 100, v % 100
        if 0 <= h < 24 and 0 <= mi < 60:
            return (h, mi), text[prefix + m.end():].strip()
    m = re.match(r"^(\d{1,2})\s*(утра|вечера|дня|ночи)", t)
    if m:
        h = int(m.group(1))
        part = m.group(2)
        if part in ("дня", "вечера") and h < 12:
            h += 12
        elif part == "ночи" and h == 12:
            h = 0
        if 0 <= h < 24:
            return (h, 0), text[prefix + m.end():].strip()
    m = re.match(r"^(\d{1,2})\b", t)
    if m:
        h = int(m.group(1))
        if 0 <= h < 24:
            return (h, 0), text[prefix + m.end():].strip()
    for word, h in DAY_PARTS.items():
        m = re.match(rf"^{word}\b", t)
        if m:
            return (h, 0), text[prefix + m.end():].strip()
    return None, text


def main_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🎀 Мои дела 🎀")
    kb.row("✨ Помощь ✨", "💖 Примеры 💖")
    return kb


def quick_time_kb():
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("🌅 9:00", callback_data="qt:9:0"),
        types.InlineKeyboardButton("☕ 11:00", callback_data="qt:11:0"),
        types.InlineKeyboardButton("🍝 13:00", callback_data="qt:13:0"),
    )
    kb.row(
        types.InlineKeyboardButton("💼 15:00", callback_data="qt:15:0"),
        types.InlineKeyboardButton("🌆 18:00", callback_data="qt:18:0"),
        types.InlineKeyboardButton("🌙 20:00", callback_data="qt:20:0"),
    )
    kb.row(types.InlineKeyboardButton("💔 Отмена", callback_data="qt:cancel"))
    return kb


def task_actions_kb(idx):
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("💅 Готово", callback_data=f"t:done:{idx}"),
        types.InlineKeyboardButton("🌸 +1д", callback_data=f"t:s1:{idx}"),
        types.InlineKeyboardButton("💔 Удалить", callback_data=f"t:del:{idx}"),
    )
    return kb


HELP_TEXT = (
    "💖✨ Хай, princess! ✨💖\n\n"
    "Я твой личный планер 🎀 Пиши дела прямо в чат, я всё пойму 💅\n\n"
    "🌸 *Как писать:*\n"
    "💖 `завтра 18:30 встреча с Олей`\n"
    "💖 `сегодня 9:00 кофе`\n"
    "💖 `25.05 14:00 маникюр`\n"
    "💖 `пятница вечером — кино`\n"
    "💖 `пн 10 утра созвон`\n\n"
    "🎀 *Что понимаю:*\n"
    "📅 _Даты:_ сегодня, завтра, послезавтра, пн-вс, 25.05, 25.05.2026\n"
    "⏰ _Время:_ 18:30, 1830, 6 вечера, утром, днём, вечером\n\n"
    "✨ Если время не указано — спрошу кнопками 💖\n\n"
    "🌟 *Команды:*\n"
    "/list — все дела\n"
    "/cancel — отменить\n"
    "/help — справка\n\n"
    "Поехали, queen 💋"
)

EXAMPLES_TEXT = (
    "💖✨ Примеры, princess! ✨💖\n\n"
    "Просто скопируй и отправь 🎀\n\n"
    "💅 `завтра 18:30 встреча`\n"
    "💅 `сегодня 9 кофе`\n"
    "💅 `25.05 маникюр` (спрошу время)\n"
    "💅 `пт вечером кино`\n"
    "💅 `послезавтра 14:00 врач`\n"
    "💅 `30.05.2026 свадьба Маши`"
)


@bot.message_handler(commands=["start", "help"])
def cmd_help(msg):
    bot.send_message(msg.chat.id, HELP_TEXT, reply_markup=main_menu(), parse_mode="Markdown")
    pending.pop(msg.chat.id, None)


@bot.message_handler(commands=["cancel"])
def cmd_cancel(msg):
    pending.pop(msg.chat.id, None)
    bot.send_message(msg.chat.id, "💔 Отменила, princess ✨", reply_markup=main_menu())


@bot.message_handler(commands=["list"])
def cmd_list(msg):
    show_list(msg.chat.id)


@bot.message_handler(func=lambda m: True)
def handle_text(msg):
    chat_id = msg.chat.id
    text = (msg.text or "").strip()

    if text == "✨ Помощь ✨":
        bot.send_message(chat_id, HELP_TEXT, reply_markup=main_menu(), parse_mode="Markdown")
        return
    if text == "💖 Примеры 💖":
        bot.send_message(chat_id, EXAMPLES_TEXT, reply_markup=main_menu(), parse_mode="Markdown")
        return
    if text == "🎀 Мои дела 🎀":
        show_list(chat_id)
        return

    pend = pending.get(chat_id)
    if pend:
        t, _ = parse_time(text)
        if t:
            save_task_with_time(chat_id, pend["title"], date.fromisoformat(pend["date"]), t)
            pending.pop(chat_id, None)
            return
        bot.send_message(chat_id,
                         "🎀 Не поняла время, princess 💔\nНапиши `18:30`, `6 вечера` или жми кнопки выше 💖",
                         parse_mode="Markdown")
        return

    d, rest = parse_date(text)
    rest = re.sub(r"^[\s\-—–:,]+", "", rest)
    t, rest = parse_time(rest)
    rest = re.sub(r"^[\s\-—–:,]+", "", rest)
    title = rest.strip()

    if not d and not t and not title:
        bot.send_message(chat_id,
                         "💔 Хмм, princess... не поняла 🎀\nЖми /help чтобы увидеть как писать ✨",
                         reply_markup=main_menu())
        return

    if not d:
        if t:
            cur = now()
            today_t = datetime(cur.year, cur.month, cur.day, t[0], t[1])
            d = cur.date() if today_t > cur else (cur + timedelta(days=1)).date()
        else:
            bot.send_message(chat_id,
                             "💔 Princess, не поняла дату 🎀\nНапиши `сегодня`, `завтра`, день недели или `25.05` 💖",
                             parse_mode="Markdown")
            return

    if not title:
        bot.send_message(chat_id, "🌸 А что за дело-то, princess? 💖\nНапиши заново с текстом 💅",
                         parse_mode="Markdown")
        return

    if not t:
        pending[chat_id] = {"title": title, "date": d.isoformat()}
        bot.send_message(chat_id,
                         f"💖 *{title}*\n🎀 на _{d.strftime('%d.%m.%Y')}_\n\n✨ Во сколько, princess? Жми кнопку или напиши время 💅",
                         reply_markup=quick_time_kb(), parse_mode="Markdown")
        return

    save_task_with_time(chat_id, title, d, t)


def save_task_with_time(chat_id, title, d, t):
    when = datetime(d.year, d.month, d.day, t[0], t[1])
    tasks = load_tasks()
    tasks.append({"when": when.isoformat(), "title": title})
    save_tasks(tasks)
    bot.send_message(chat_id,
                     f"💅✨ Добавила, queen! ✨💅\n\n🌸 *{title}*\n🎀 {when.strftime('%d.%m.%Y в %H:%M')} 💖",
                     reply_markup=main_menu(), parse_mode="Markdown")


@bot.callback_query_handler(func=lambda c: True)
def handle_callback(call):
    chat_id = call.message.chat.id
    data = call.data
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    if data.startswith("qt:"):
        if data == "qt:cancel":
            pending.pop(chat_id, None)
            try:
                bot.edit_message_text("💔 Отменила, princess ✨", chat_id, call.message.message_id)
            except Exception:
                pass
            return
        _, h, m = data.split(":")
        pend = pending.get(chat_id)
        if not pend:
            return
        try:
            bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
        except Exception:
            pass
        save_task_with_time(chat_id, pend["title"],
                            date.fromisoformat(pend["date"]), (int(h), int(m)))
        pending.pop(chat_id, None)
        return

    if data.startswith("t:"):
        _, action, idx_s = data.split(":")
        idx = int(idx_s)
        tasks_s = sorted(load_tasks(), key=lambda x: x["when"])
        if idx < 0 or idx >= len(tasks_s):
            return
        task = tasks_s[idx]

        if action == "done":
            full = [x for x in load_tasks() if not (x["when"] == task["when"] and x["title"] == task["title"])]
            save_tasks(full)
            try:
                bot.edit_message_text(f"💅✨ Готово, queen! ✨💅\n~{task['title']}~",
                                      chat_id, call.message.message_id, parse_mode="Markdown")
            except Exception:
                pass

        elif action == "del":
            full = [x for x in load_tasks() if not (x["when"] == task["when"] and x["title"] == task["title"])]
            save_tasks(full)
            try:
                bot.edit_message_text(f"💔 Удалила, princess\n_{task['title']}_",
                                      chat_id, call.message.message_id, parse_mode="Markdown")
            except Exception:
                pass

        elif action == "s1":
            when = datetime.fromisoformat(task["when"]) + timedelta(days=1)
            full = load_tasks()
            for x in full:
                if x["when"] == task["when"] and x["title"] == task["title"]:
                    x["when"] = when.isoformat()
                    break
            save_tasks(full)
            try:
                bot.edit_message_text(
                    f"🌸✨ Отложила на +1 день! ✨🌸\n\n💖 *{task['title']}*\n🎀 теперь {when.strftime('%d.%m.%Y в %H:%M')}",
                    chat_id, call.message.message_id, parse_mode="Markdown")
            except Exception:
                pass


def show_list(chat_id):
    cleanup_old()
    tasks = sorted(load_tasks(), key=lambda t: t["when"])
    if not tasks:
        bot.send_message(chat_id,
                         "💖✨ Дел пока нет, princess! ✨💖\n\n🎀 Просто напиши `завтра 18:30 встреча` 💅",
                         reply_markup=main_menu(), parse_mode="Markdown")
        return
    bot.send_message(chat_id, "🌸✨ 「 ТВОИ ПЛАНЫ, QUEEN 」 ✨🌸", reply_markup=main_menu())
    for i, t in enumerate(tasks):
        dt = datetime.fromisoformat(t["when"])
        bot.send_message(chat_id,
                         f"💖 *{t['title']}*\n🎀 {dt.strftime('%d.%m.%Y в %H:%M')}",
                         reply_markup=task_actions_kb(i), parse_mode="Markdown")


def reminders_loop():
    while True:
        try:
            state = load_state()
            n = now()
            today_s = n.date().isoformat()

            if n.weekday() == 6 and n.hour == 21 and state.get("reminded_sunday") != today_s:
                bot.send_message(MY_ID,
                                 "🌸✨💖 Хай, princess! 💖✨🌸\n\nВоскресенье вечер 🎀\nДавай напишем план на неделю, queen 💅\n\nПросто скидывай дела типа `пн 10:00 созвон` ✨",
                                 reply_markup=main_menu(), parse_mode="Markdown")
                state["reminded_sunday"] = today_s
                save_state(state)

            if n.hour == 20 and state.get("reminded_daily") != today_s:
                tomorrow = (n + timedelta(days=1)).date()
                upcoming = []
                for t in load_tasks():
                    try:
                        if datetime.fromisoformat(t["when"]).date() == tomorrow:
                            upcoming.append(t)
                    except Exception:
                        pass
                if upcoming:
                    upcoming.sort(key=lambda x: x["when"])
                    lines = ["💅✨ Хай, queen! ✨💅\n\nЗавтра у тебя, princess 🎀💖\n"]
                    for t in upcoming:
                        dt = datetime.fromisoformat(t["when"])
                        lines.append(f"🌸 *{dt.strftime('%H:%M')}* — {t['title']}")
                    lines.append("\n💖 Не забудь, queen ✨")
                    bot.send_message(MY_ID, "\n".join(lines), parse_mode="Markdown")
                state["reminded_daily"] = today_s
                save_state(state)
        except Exception as e:
            print(f"reminders error: {e}", file=sys.stderr)
        time.sleep(60)


if __name__ == "__main__":
    print("💖✨ Bimbo Planner запущен. Слушаю Telegram ✨💖", flush=True)
    threading.Thread(target=reminders_loop, daemon=True).start()
    bot.infinity_polling()

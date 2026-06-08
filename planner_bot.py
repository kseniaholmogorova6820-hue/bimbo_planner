# -*- coding: utf-8 -*-
"""
💖✨ Бот-планировщик princess edition v2 ✨💖
Главный режим — текстовый ввод дел.
Запускается раз в N минут через Scheduled Task на PythonAnywhere.

Примеры ввода:
  завтра 18:30 встреча с Олей
  сегодня 9:00 кофе с подругой
  25.05 14:00 маникюр
  пятница вечером — кино
  пн 10 утра созвон
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, date

import telebot
from telebot import types

# ====== НАСТРОЙКИ ======
# Токен и ID берутся из переменных окружения (для безопасности)
# Локально создайте файл .env рядом со скриптом:
#   TOKEN=ваш_токен_от_BotFather
#   MY_ID=ваш_telegram_id
TOKEN = os.environ.get("TOKEN", "")
MY_ID = int(os.environ.get("MY_ID", "0"))
TZ_OFFSET = int(os.environ.get("TZ_OFFSET", "3"))  # МСК = UTC+3

# Если .env лежит рядом со скриптом — подгрузим оттуда
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
    print("ОШИБКА: укажите TOKEN и MY_ID в файле .env или в переменных окружения", file=sys.stderr)
    sys.exit(1)

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(HERE, "tasks.json")
STATE_FILE = os.path.join(HERE, "state.json")
UPDATES_FILE = os.path.join(HERE, "updates.json")

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


# ===================== ВРЕМЯ =====================
def now():
    return datetime.utcnow() + timedelta(hours=TZ_OFFSET)


# ===================== ХРАНИЛИЩЕ =====================
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
    return _load(STATE_FILE, {
        "pending": {},
        "reminded_sunday": "",
        "reminded_daily": "",
    })


def save_state(state):
    _save(STATE_FILE, state)


def load_last_update_id():
    return _load(UPDATES_FILE, {"last_id": 0}).get("last_id", 0)


def save_last_update_id(uid):
    _save(UPDATES_FILE, {"last_id": uid})


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


# ===================== ПАРСЕР =====================
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
        if part == "утра":
            pass
        elif part in ("дня", "вечера"):
            if h < 12:
                h += 12
        elif part == "ночи":
            if h == 12:
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


def parse_input(text):
    raw = text.strip()
    if raw.startswith("/"):
        return None

    d, rest = parse_date(raw)
    rest = re.sub(r"^[\s\-—–:,]+", "", rest)

    t, rest = parse_time(rest)
    rest = re.sub(r"^[\s\-—–:,]+", "", rest)

    title = rest.strip()
    return {"date": d, "time": t, "title": title}


# ===================== КЛАВИАТУРЫ =====================
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


def task_actions_kb(task_idx):
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("💅 Готово", callback_data=f"t:done:{task_idx}"),
        types.InlineKeyboardButton("🌸 +1д", callback_data=f"t:s1:{task_idx}"),
        types.InlineKeyboardButton("💔 Удалить", callback_data=f"t:del:{task_idx}"),
    )
    return kb


# ===================== ОТПРАВКА =====================
def send(chat_id, text, kb=None, parse_mode=None):
    try:
        bot.send_message(chat_id, text, reply_markup=kb, parse_mode=parse_mode)
    except Exception as e:
        print(f"send error: {e}", file=sys.stderr)


# ===================== ЛОГИКА =====================
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


def handle_message(msg, state):
    chat_id = msg.chat.id
    text = (msg.text or "").strip()

    if text in ("/start", "/help", "✨ Помощь ✨"):
        send(chat_id, HELP_TEXT, kb=main_menu(), parse_mode="Markdown")
        state["pending"].pop(str(chat_id), None)
        return

    if text == "💖 Примеры 💖":
        send(chat_id, EXAMPLES_TEXT, kb=main_menu(), parse_mode="Markdown")
        return

    if text == "/cancel":
        state["pending"].pop(str(chat_id), None)
        send(chat_id, "💔 Отменила, princess ✨", kb=main_menu())
        return

    if text in ("/list", "🎀 Мои дела 🎀"):
        show_list(chat_id)
        return

    pend = state["pending"].get(str(chat_id))
    if pend:
        t, rest = parse_time(text)
        if t:
            save_task_with_time(chat_id, pend["title"], date.fromisoformat(pend["date"]), t)
            state["pending"].pop(str(chat_id), None)
            return
        send(chat_id,
             "🎀 Не поняла время, princess 💔\n"
             "Напиши типа `18:30`, `6 вечера` или жми кнопки выше 💖",
             parse_mode="Markdown")
        return

    parsed = parse_input(text)
    if parsed is None:
        send(chat_id,
             "💔 Хмм, princess... не поняла 🎀\n"
             "Жми /help чтобы увидеть как писать ✨",
             kb=main_menu())
        return

    d, t, title = parsed["date"], parsed["time"], parsed["title"]

    if not d and not t and not title:
        send(chat_id, "💔 Напиши дело, princess 💖\nПример: `завтра 18:30 встреча`",
             kb=main_menu(), parse_mode="Markdown")
        return

    if not d:
        if t:
            cur = now()
            today_t = datetime(cur.year, cur.month, cur.day, t[0], t[1])
            d = cur.date() if today_t > cur else (cur + timedelta(days=1)).date()
        else:
            send(chat_id,
                 "💔 Princess, не поняла дату 🎀\n"
                 "Напиши `сегодня`, `завтра`, день недели или дату типа `25.05` 💖",
                 parse_mode="Markdown")
            return

    if not title:
        send(chat_id,
             "🌸 А что за дело-то, princess? 💖\n"
             "Напиши заново с текстом: `завтра 18:30 встреча с Олей`",
             parse_mode="Markdown")
        return

    if not t:
        state["pending"][str(chat_id)] = {"title": title, "date": d.isoformat()}
        send(chat_id,
             f"💖 *{title}*\n"
             f"🎀 на _{d.strftime('%d.%m.%Y')}_\n\n"
             f"✨ Во сколько, princess? Жми кнопку или напиши время 💅",
             kb=quick_time_kb(), parse_mode="Markdown")
        return

    save_task_with_time(chat_id, title, d, t)


def save_task_with_time(chat_id, title, d, t):
    when = datetime(d.year, d.month, d.day, t[0], t[1])
    tasks = load_tasks()
    tasks.append({"when": when.isoformat(), "title": title})
    save_tasks(tasks)
    send(chat_id,
         f"💅✨ Добавила, queen! ✨💅\n\n"
         f"🌸 *{title}*\n"
         f"🎀 {when.strftime('%d.%m.%Y в %H:%M')} 💖",
         kb=main_menu(), parse_mode="Markdown")


def handle_callback(call, state):
    chat_id = call.message.chat.id
    data = call.data

    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    if data.startswith("qt:"):
        if data == "qt:cancel":
            state["pending"].pop(str(chat_id), None)
            try:
                bot.edit_message_text("💔 Отменила, princess ✨",
                                      chat_id, call.message.message_id)
            except Exception:
                pass
            return
        _, h, m = data.split(":")
        h, m = int(h), int(m)
        pend = state["pending"].get(str(chat_id))
        if not pend:
            return
        try:
            bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
        except Exception:
            pass
        save_task_with_time(chat_id, pend["title"],
                            date.fromisoformat(pend["date"]), (h, m))
        state["pending"].pop(str(chat_id), None)
        return

    if data.startswith("t:"):
        _, action, idx_s = data.split(":")
        idx = int(idx_s)
        tasks = sorted(load_tasks(), key=lambda t: t["when"])
        if idx < 0 or idx >= len(tasks):
            return
        task = tasks[idx]

        if action == "done":
            full = load_tasks()
            full = [x for x in full if not (x["when"] == task["when"] and x["title"] == task["title"])]
            save_tasks(full)
            try:
                bot.edit_message_text(
                    f"💅✨ Готово, queen! ✨💅\n~{task['title']}~",
                    chat_id, call.message.message_id, parse_mode="Markdown")
            except Exception:
                pass

        elif action == "del":
            full = load_tasks()
            full = [x for x in full if not (x["when"] == task["when"] and x["title"] == task["title"])]
            save_tasks(full)
            try:
                bot.edit_message_text(
                    f"💔 Удалила, princess\n_{task['title']}_",
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
                    f"🌸✨ Отложила на +1 день! ✨🌸\n\n"
                    f"💖 *{task['title']}*\n"
                    f"🎀 теперь {when.strftime('%d.%m.%Y в %H:%M')}",
                    chat_id, call.message.message_id, parse_mode="Markdown")
            except Exception:
                pass
        return


def show_list(chat_id):
    cleanup_old()
    tasks = sorted(load_tasks(), key=lambda t: t["when"])
    if not tasks:
        send(chat_id,
             "💖✨ Дел пока нет, princess! ✨💖\n\n"
             "🎀 Просто напиши что-то типа `завтра 18:30 встреча` 💅",
             kb=main_menu(), parse_mode="Markdown")
        return

    send(chat_id, "🌸✨ 「 ТВОИ ПЛАНЫ, QUEEN 」 ✨🌸", kb=main_menu())
    for i, t in enumerate(tasks):
        dt = datetime.fromisoformat(t["when"])
        text = (f"💖 *{t['title']}*\n"
                f"🎀 {dt.strftime('%d.%m.%Y в %H:%M')}")
        send(chat_id, text, kb=task_actions_kb(i), parse_mode="Markdown")


def check_reminders(state):
    n = now()
    today_s = n.date().isoformat()

    if n.weekday() == 6 and n.hour == 21 and state.get("reminded_sunday") != today_s:
        send(MY_ID,
             "🌸✨💖 Хай, princess! 💖✨🌸\n\n"
             "Воскресенье вечер 🎀\n"
             "Давай напишем план на неделю, queen 💅\n\n"
             "Просто скидывай дела типа `пн 10:00 созвон` ✨",
             kb=main_menu(), parse_mode="Markdown")
        state["reminded_sunday"] = today_s

    if n.hour == 20 and state.get("reminded_daily") != today_s:
        tomorrow = (n + timedelta(days=1)).date()
        tasks = load_tasks()
        upcoming = []
        for t in tasks:
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
            send(MY_ID, "\n".join(lines), parse_mode="Markdown")
        state["reminded_daily"] = today_s


def main():
    state = load_state()
    last_id = load_last_update_id()
    try:
        updates = bot.get_updates(offset=last_id + 1, timeout=10)
    except Exception as e:
        print(f"get_updates error: {e}", file=sys.stderr)
        updates = []

    for upd in updates:
        try:
            if upd.message:
                handle_message(upd.message, state)
            elif upd.callback_query:
                handle_callback(upd.callback_query, state)
        except Exception as e:
            print(f"handle error: {e}", file=sys.stderr)
        last_id = upd.update_id

    if updates:
        save_last_update_id(last_id)

    try:
        check_reminders(state)
    except Exception as e:
        print(f"reminders error: {e}", file=sys.stderr)

    save_state(state)


if __name__ == "__main__":
    main()

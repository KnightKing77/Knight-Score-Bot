VERSION = "SCORE-PARSER-FIX-V3"
import os
import re
import time
import json
from pathlib import Path

import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager


# ============================================================
# CONFIG
# ============================================================

CHANNEL_ID = "@knightbotlive"
POLL_SECONDS = 5
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

if not BOT_TOKEN:
    raise RuntimeError(
        "Telegram token missing.\n"
        "Run:\n"
        "set TELEGRAM_BOT_TOKEN=YOUR_NEW_TOKEN"
    )

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

BASE_DIR = Path(__file__).resolve().parent
SCOREBOARD_FILE = BASE_DIR / "scoreboard.json"


# ============================================================
# TELEGRAM
# ============================================================

def telegram(method, payload=None):
    response = requests.post(
        f"{TG_API}/{method}",
        json=payload or {},
        timeout=30,
    )
    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(data.get("description", "Telegram API error"))

    return data["result"]


def check_telegram():
    bot = telegram("getMe")

    print(
        f"🤖 Bot: @{bot.get('username', bot.get('first_name', 'unknown'))}"
    )

    telegram(
        "deleteWebhook",
        {"drop_pending_updates": False},
    )

    member = telegram(
        "getChatMember",
        {
            "chat_id": CHANNEL_ID,
            "user_id": bot["id"],
        },
    )

    status = member.get("status")
    print(f"📢 Channel: {CHANNEL_ID}")
    print(f"👤 Bot status: {status}")

    if status not in ("administrator", "creator"):
        raise RuntimeError(
            f"Bot must be ADMIN of {CHANNEL_ID}."
        )

    if status == "administrator" and not member.get(
        "can_post_messages", False
    ):
        raise RuntimeError(
            "Bot is admin but Post Messages permission is OFF."
        )

    print("✅ Telegram connection ready")


def post_message(text):
    return telegram(
        "sendMessage",
        {
            "chat_id": CHANNEL_ID,
            "text": text,
            "disable_web_page_preview": True,
        },
    )


# ============================================================
# CREX URL / TEAM HELPERS
# ============================================================

def ask_crex_url():
    while True:
        url = input("\nPaste CREX URL:\n> ").strip()

        if not url:
            print("❌ URL cannot be empty.")
            continue

        if not url.startswith("https://crex.com/"):
            print("❌ Please paste a CREX URL.")
            continue

        return url


def clean(value):
    if not value:
        return "-"
    return " ".join(value.split()).strip()


def find_first(patterns, text):
    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            re.I | re.S,
        )
        if match:
            return clean(match.group(1))

    return "-"


def teams_from_url(url):
    match = re.search(
        r"/([a-z0-9]+)-vs-([a-z0-9]+)-",
        url,
        re.I,
    )

    if match:
        return (
            match.group(1).upper(),
            match.group(2).upper(),
        )

    return "-", "-"


# ============================================================
# FORMAT-AGNOSTIC SCORE PARSER
# ============================================================

def parse_scoreboard(title, page_text, crex_url):
    title_clean = re.sub(r"\s+", " ", title).strip()
    page_clean = re.sub(r"\s+", " ", page_text).strip()

    url_team1, url_team2 = teams_from_url(crex_url)

    team1 = url_team1
    team2 = url_team2
    team1_score = "-"
    team2_score = "-"
    team1_overs = "-"
    team2_overs = "-"

    status = "LIVE"
    result_text = "-"

    # --------------------------------------------------------
    # LIVE CREX TITLE
    #
    # Example:
    # SLK 44-1 (5.0) (John Campbell 10(5), Kamil Pooran 29(20))
    # vs Guyana Amazon Warriors 31st-Match | ...
    #
    # The second innings score may NOT exist in the title while
    # the match is live, so only parse the current scoreboard.
    # --------------------------------------------------------
    live = re.search(
        r"(?P<team>[A-Za-z][A-Za-z0-9 .&'_-]{0,20}?)\s+"
        r"(?P<runs>\d+)\s*[-/]\s*(?P<wickets>\d+)\s*"
        r"\(\s*(?P<overs>\d+(?:\.\d+)?)\s*\)"
        r"\s*(?:\([^)]*\))?\s*"
        r"\bvs\b",
        title_clean,
        re.I,
    )

    if live:
        raw_team = clean(live.group("team"))
        # Prefer the short code from the URL when available.
        team1 = url_team1 if url_team1 != "-" else raw_team.upper()
        team1_score = f"{live.group('runs')}/{live.group('wickets')}"
        team1_overs = live.group("overs")

    else:
        # ----------------------------------------------------
        # Finished / alternate title formats containing both
        # scoreboards.
        # ----------------------------------------------------
        both = re.search(
            r"(?P<team1>[A-Za-z][A-Za-z0-9 .&'_-]{0,35}?)\s+"
            r"(?P<r1>\d+)[-/](?P<w1>\d+)\s*"
            r"\((?P<o1>\d+(?:\.\d+)?)\)"
            r".{0,180}?\bvs\b\s+"
            r"(?P<team2>[A-Za-z][A-Za-z0-9 .&'_-]{0,45}?)\s+"
            r"(?P<r2>\d+)[-/](?P<w2>\d+)\s*"
            r"\(\s*\(?(?P<o2>\d+(?:\.\d+)?)\)?\)",
            title_clean,
            re.I,
        )
        if both:
            team1 = clean(both.group("team1"))
            team1_score = f"{both.group('r1')}/{both.group('w1')}"
            team1_overs = both.group("o1")
            team2 = clean(both.group("team2"))
            team2_score = f"{both.group('r2')}/{both.group('w2')}"
            team2_overs = both.group("o2")

    # Body fallback: use the URL's first team code and nearby
    # scoreboard if the title format changes.
    if team1_score == "-":
        patterns = [
            r"\b([A-Z][A-Z0-9]{1,10})\s+(\d+)-(\d+)\s+\((\d+(?:\.\d+)?)\)",
            r"\b([A-Z][A-Z0-9]{1,10})\s+(\d+)/(\d+)\s+\((\d+(?:\.\d+)?)\)",
        ]
        for pattern in patterns:
            match = re.search(pattern, page_clean, re.I)
            if match:
                team1 = match.group(1).upper()
                team1_score = f"{match.group(2)}/{match.group(3)}"
                team1_overs = match.group(4)
                break

    # Result
    result_match = re.search(
        r"([A-Za-z][A-Za-z .&'-]+?)\s+"
        r"(won by\s+\d+\s+(?:wickets?|runs?))",
        title_clean,
        re.I,
    )
    if result_match:
        status = "FINISHED"
        result_text = clean(result_match.group(2))

    # Live stats
    crr = find_first(
        [r"\bCRR\s*:\s*([0-9.]+)", r"\bCRR\s+([0-9.]+)"],
        page_text,
    )
    rrr = find_first(
        [r"\bRRR\s*:\s*([0-9.]+)", r"\bRRR\s+([0-9.]+)"],
        page_text,
    )

    partnership = find_first(
        [r"P'?ship\s*:\s*([^\n]+)", r"Partnership\s*:\s*([^\n]+)"],
        page_text,
    )
    last_wicket = find_first(
        [r"Last\s+Wkt\s*:\s*([^\n]+)", r"Last\s+Wicket\s*:\s*([^\n]+)"],
        page_text,
    )
    toss = find_first(
        [r"([A-Za-z .&'-]+?\s+opt\s+to\s+(?:bat|bowl))"],
        page_text,
    )
    target = find_first(
        [
            r"([A-Za-z0-9 .&'-]+?\s+need(?:s)?\s+\d+\s+runs?.{0,100}?(?:balls|overs))",
            r"(\d+\s+runs?\s+required.{0,100}?(?:balls|overs)?)",
        ],
        page_text,
    )

    return {
        "team1": team1,
        "team2": team2,
        "team1_score": team1_score,
        "team2_score": team2_score,
        "team1_overs": team1_overs,
        "team2_overs": team2_overs,
        "status": status,
        "result": result_text,
        "crr": crr,
        "rrr": rrr,
        "partnership": partnership,
        "last_wicket": last_wicket,
        "target": target,
        "toss": toss,
    }


# ============================================================
# BALL / OVER / BATSMAN / BOWLER
# ============================================================

def extract_current_over(page_text):
    """Extract the latest ball/over number such as 13.5 or 20.0."""
    patterns = [
        r"\b(\d+\.\d+)\b",
        r"\bOver\s+(\d+(?:\.\d+)?)",
    ]
    for pattern in patterns:
        matches = re.findall(pattern, page_text, re.I)
        if matches:
            return matches[-1]
    return "-"


def extract_over_details(page_text):
    patterns = [
        r"Over\s+\d+\s+(.+?)\s*=\s*\d+",
        r"This\s+over\s*[:\-]?\s*(.+?)(?:\n|$)",
        r"Current\s+over\s*[:\-]?\s*(.+?)(?:\n|$)",
    ]

    for pattern in patterns:

        matches = re.findall(
            pattern,
            page_text,
            re.I | re.S,
        )

        if matches:

            value = clean(
                matches[-1]
            )

            value = re.sub(
                r"\s*=\s*\d+\s*$",
                "",
                value,
            )

            return value

    return "-"


def latest_ball_token(page_text):

    details = extract_over_details(
        page_text
    )

    if details == "-":
        return "-"

    tokens = details.split()

    if not tokens:
        return "-"

    return tokens[-1].upper()


def ball_event(page_text):

    token = latest_ball_token(
        page_text
    )

    if token in {
        "W",
        "WK",
        "OUT",
        "WICKET",
    }:
        return "OUT"

    if token in {
        "WD",
        "WIDE",
    }:
        return "WIDE"

    if token in {
        "NB",
        "NO-BALL",
        "NO_BALL",
    }:
        return "NO BALL"

    if token in {
        "4",
        "FOUR",
    }:
        return "FOUR"

    if token in {
        "6",
        "SIX",
    }:
        return "SIX"

    if token.isdigit():
        return "RUN"

    return "BALL"


def ball_runs(page_text):

    token = latest_ball_token(
        page_text
    )

    if token in {
        "W",
        "WK",
        "OUT",
        "WICKET",
        "WD",
        "WIDE",
        "NB",
        "NO-BALL",
        "NO_BALL",
    }:
        return "0"

    if token == "FOUR":
        return "4"

    if token == "SIX":
        return "6"

    if token.isdigit():
        return token

    return "0"


def striker(page_text, title):

    players = re.findall(
        r"([A-Za-z][A-Za-z .'-]{1,30})"
        r"\s+(\d+)\((\d+)\)",
        title,
    )

    if players:

        p = players[0]

        return (
            f"{clean(p[0])} "
            f"{p[1]}({p[2]})"
        )

    return find_first(
        [
            r"([A-Za-z][A-Za-z .'-]{1,30})\s+ON\s+STRIKE",
            r"Striker\s*:\s*([A-Za-z][A-Za-z .'-]{1,30})",
        ],
        page_text,
    )


def bowler(page_text):

    matches = re.findall(
        r"(?:^|\n)\s*"
        r"([A-Za-z][A-Za-z.' -]{1,30})\s*\n"
        r"\s*(\d+(?:-\d+)?)\s*\n"
        r"\s*\(([\d.]+)\)",
        page_text,
        re.I,
    )

    if matches:

        name, figures, overs = matches[-1]

        return (
            f"{clean(name)} "
            f"{figures} ({overs})"
        )

    match = re.search(
        r"\b([A-Za-z][A-Za-z.' -]{1,30})\s+"
        r"(\d+-\d+)\s*\(([\d.]+)\)",
        page_text,
        re.I,
    )

    if match:

        return (
            f"{clean(match.group(1))} "
            f"{match.group(2)} "
            f"({match.group(3)})"
        )

    return "-"


def open_number_view(driver):
    """
    CREX renders Number View dynamically. Click the tab and return the
    updated visible body text so the probability/money row is available.
    """
    try:
        elements = driver.find_elements(
            "xpath",
            "//*[normalize-space()='Number View']"
        )

        if not elements:
            elements = driver.find_elements(
                "xpath",
                "//button[contains(normalize-space(.),'Number View')]"
            )

        for element in elements:
            try:
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});",
                    element,
                )
                driver.execute_script(
                    "arguments[0].click();",
                    element,
                )
                time.sleep(0.4)
                break
            except Exception:
                continue

    except Exception as error:
        print("⚠️ Number View click:", error)

    return driver.find_element("tag name", "body").text


# ============================================================
# NUMBER VIEW — DYNAMIC / FORMAT AGNOSTIC
# ============================================================

def number_view(page_text):
    """
    Return only CREX Number View's first probability row.

    Example from CREX:
        St Lucia Kings   45   46
        15 Ov Runs       120  121
        20 Ov Runs       163  164

    Telegram should show only:
        💰 45 46
    """
    text = re.sub(r"\s+", " ", page_text).strip()

    # Prefer a known team row immediately followed by two values.
    known = re.search(
        r"\b("
        r"St Lucia Kings|"
        r"Jamaica Tallawahs|"
        r"Trinbago Knight Riders|"
        r"Guyana Amazon Warriors|"
        r"Barbados Royals|"
        r"St Kitts(?: &| and)? Nevis Patriots|"
        r"Antigua(?: &| and)? Barbuda Falcons"
        r")\s+"
        r"(\d+(?:\.\d+)?)\s+"
        r"(\d+(?:\.\d+)?)\b",
        text,
        re.I,
    )
    if known:
        return f"💰 {known.group(2)} {known.group(3)}"

    # Generic fallback: take the first two numeric values before the
    # first projection row.
    cutoff = re.search(
        r"\b(?:10|15|20|25|30|40|50)\s*Ov\s*Runs?\b",
        text,
        re.I,
    )
    before = text[:cutoff.start()] if cutoff else text

    generic = re.search(
        r"\b([A-Za-z][A-Za-z .&'-]{2,40})\s+"
        r"(\d+(?:\.\d+)?)\s+"
        r"(\d+(?:\.\d+)?)\b",
        before,
        re.I,
    )
    if generic:
        return f"💰 {generic.group(2)} {generic.group(3)}"

    return "💰 -"


# ============================================================
# MESSAGE FORMAT
# ============================================================


def extract_number_view_row(page_text):
    """
    Extract ONLY the CREX Number View team probability row.

    Desired:
        54-55 🇩🇪 ST LUCIA KINGS 🇩🇪

    Never use:
        6 OVER
        20 OVER
        2-0 OVER
        10 Ov Runs / 15 Ov Runs / 20 Ov Runs
    """
    raw = page_text
    flat = re.sub(r"\s+", " ", raw).strip()

    # These are the team names CREX can show in CPL. We search for the team
    # first, then inspect only a small window around that team.
    known_teams = [
        "St Lucia Kings",
        "Jamaica Kingsmen",
        "Jamaica Tallawahs",
        "Trinbago Knight Riders",
        "Guyana Amazon Warriors",
        "Barbados Royals",
        "St Kitts and Nevis Patriots",
        "St Kitts & Nevis Patriots",
        "Antigua and Barbuda Falcons",
        "Antigua & Barbuda Falcons",
    ]

    # First try line-by-line. CREX often exposes the probability row as:
    # St Lucia Kings    54    55
    lines = [clean(x) for x in raw.splitlines() if clean(x)]

    for team in known_teams:
        for i, line in enumerate(lines):
            if team.lower() not in line.lower():
                continue

            # Look at this line and the next few lines only.
            window = " ".join(lines[i:i+4])

            # Strip UI labels that can sit between the team and its numbers.
            window = re.sub(
                r"\b(?:PROBABILITY|NUMBER\s+VIEW|VIEW\s+NUMBER\s+VIEW)\b",
                " ",
                window,
                flags=re.I,
            )
            window = re.sub(r"\s+", " ", window).strip()

            # Capture two numbers belonging to the team row, but reject
            # values immediately associated with "Ov Runs" / "OVER".
            m = re.search(
                re.escape(team) +
                r".{0,100}?"
                r"(?<![\d.])(\d+(?:\.\d+)?)\s+"
                r"(\d+(?:\.\d+)?)(?![\d.])",
                window,
                re.I,
            )

            if m:
                a, b = m.group(1), m.group(2)

                # Do not accept an over/projection pair.
                before_pair = window[:m.start(1)]
                if re.search(
                    r"\b(?:\d+\s*)?Ov\s*Runs?\b|\bOVER\b",
                    before_pair,
                    re.I,
                ):
                    continue

                return clean(team), a, b

    # Flattened-text fallback. Restrict the match so "2-0 OVER" or
    # "41 42 6 OVER" cannot become the team probability row.
    for team in known_teams:
        m = re.search(
            re.escape(team) +
            r"(?!.*\b(?:Ov\s+Runs?|OVER)\b.{0,20})"
            r".{0,80}?"
            r"(\d+(?:\.\d+)?)\s+"
            r"(\d+(?:\.\d+)?)",
            flat,
            re.I,
        )
        if m:
            a, b = m.group(1), m.group(2)

            # Probability values should be plain numbers, not an over label.
            context = flat[m.start():m.end()+25]
            if re.search(r"\b(?:\d+\s*)?Ov\s+Runs?\b|\bOVER\b", context, re.I):
                continue

            return clean(team), a, b

    return "-", "-", "-"


def extract_projection_row(page_text):
    text = re.sub(r"\s+", " ", page_text).strip()
    m = re.search(r"(\d+)\s*Ov\s*Runs\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)", text, re.I)
    if m:
        return f"{m.group(2)}-{m.group(3)} 👉🔘 {m.group(1)} OVER 🔘"
    return "-"


def make_messages(score, page_text, title):
    # IMPORTANT: use the over attached to the actual scoreboard.
    # extract_current_over() can accidentally return a bowler over such as
    # 0.2 because it searches all page numbers.
    over = score.get("team1_overs", "-")
    current_score = score.get("team1_score", "-")
    total_score = f"{current_score} ({over})"

    bat = striker(page_text, title)
    striker_msg = f"{bat.upper()} ON STRIKE ✔️" if bat != "-" else "ON STRIKE ✔️"

    event = ball_event(page_text)
    event_msg = {
        "FOUR":"FOUR", "SIX":"SIX", "OUT":"OUT",
        "WIDE":"WIDE BALL", "NO BALL":"NO BALL", "RUN":"BALL"
    }.get(event, "BALL")

    nv_team, nv_a, nv_b = extract_number_view_row(page_text)
    projection = extract_projection_row(page_text)
    rr = score.get("crr", "-")
    bowl = bowler(page_text)
    bowler_msg = f"🥎 {bowl}" if bowl != "-" else "🥎 -"

    messages = [
        "🥎︎.",
        total_score,
        striker_msg,
        event_msg,
        ball_runs(page_text),
    ]

    if projection != "-":
        messages.append(projection)

    if nv_team != "-":
        messages.append(
            f"{nv_a}-{nv_b} 🇩🇪 {nv_team.upper()} 🇩🇪"
        )

    if rr != "-":
        messages.append(
            f"RUN RATE PER OVER 🔥 {rr}"
        )

    if bowl != "-":
        messages.append(bowler_msg)

    if is_over_complete(page_text, over):
        card = build_score_card(page_text, last_over_runs(page_text))
        if card:
            messages.append(card)
    return messages


def extract_over_tokens(page_text):
    details = extract_over_details(page_text)
    if details == "-":
        return []
    return details.split()


def last_over_runs(page_text):
    total = 0
    found = False
    for t in extract_over_tokens(page_text):
        t=t.upper()
        if t in {"WD","NB"}:
            total += 1; found=True
        elif t.isdigit():
            total += int(t); found=True
    return str(total) if found else "-"


def is_over_complete(page_text, over):
    tokens=extract_over_tokens(page_text)
    legal=sum(1 for t in tokens if t.upper() in {"W","0","1","2","3","4","5","6"})
    return legal >= 6


def build_score_card(page_text, over_runs):
    tokens=extract_over_tokens(page_text)
    if not tokens:
        return None
    batsmen=re.findall(r"\b([A-Za-z][A-Za-z .'-]{1,30})\s+(\d+)\((\d+)\)",page_text)
    lines=[f"SCORE CARD :-{over_runs}","", " ".join(tokens)]
    for name,runs,balls in batsmen[:2]:
        parts=clean(name).split()
        short=f"{parts[0][0]}-{parts[-1]}".upper() if len(parts)>1 else parts[0].upper()
        lines.append(f"{short:<15}:-{runs}({balls})")
    return "\n".join(lines)


# ============================================================
# SCOREBOARD SAVE
# ============================================================

def save_score(score):

    temp = SCOREBOARD_FILE.with_suffix(
        ".tmp"
    )

    with open(
        temp,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            score,
            f,
            indent=2,
            ensure_ascii=False,
        )

    temp.replace(
        SCOREBOARD_FILE
    )


# ============================================================
# BROWSER
# ============================================================

def start_browser(url):

    print("🌐 Starting Chrome...")

    driver = webdriver.Chrome(
        service=Service(
            ChromeDriverManager().install()
        )
    )

    driver.get(url)

    time.sleep(7)

    return driver


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "======================================"
    )
    print(
        "🏏 KNIGHT UNIVERSAL CREX BOT"
    )
    print(f"🔧 Version: {VERSION}")
    print(
        "======================================"
    )

    crex_url = ask_crex_url()

    print(
        "\n🔗 Match:",
        crex_url,
    )

    print(
        "📢 Telegram:",
        CHANNEL_ID,
    )

    print(
        "⏱️ Update:",
        POLL_SECONDS,
        "seconds",
    )

    check_telegram()

    driver = None

    try:

        driver = start_browser(
            crex_url
        )

        while True:

            try:

                title = driver.title

                # Open CREX Number View before every scrape so the dynamic
                # probability/projection rows are available.
                page_text = open_number_view(driver)

                score = parse_scoreboard(
                    title,
                    page_text,
                    crex_url,
                )

                if score["team1_score"] == "-":

                    print(
                        "⚠️ Score not detected yet."
                    )

                    print(
                        "   Title:",
                        title,
                    )

                else:

                    print(
                        f"🏏 {score['team1']} "
                        f"{score['team1_score']} "
                        f"({score['team1_overs']}) "
                        f"vs "
                        f"{score['team2']} "
                        f"{score['team2_score']} "
                        f"({score['team2_overs']})"
                    )

                    save_score(
                        score
                    )

                    messages = make_messages(
                        score,
                        page_text,
                        title,
                    )

                    for index, message in enumerate(
                        messages,
                        start=1,
                    ):

                        try:

                            result = post_message(
                                message
                            )

                            print(
                                f"📩 POST {index}: "
                                f"{result.get('message_id')}"
                            )

                        except Exception as telegram_error:

                            print(
                                "❌ Telegram:",
                                telegram_error,
                            )

                        time.sleep(
                            0.8
                        )

                time.sleep(
                    POLL_SECONDS
                )

                # Refresh to get fresh CREX data.
                driver.refresh()

                time.sleep(2)

            except Exception as error:

                error_text = str(
                    error
                )

                print(
                    "⚠️ Browser error:",
                    error_text,
                )

                # Selenium session can die. Recreate it automatically.
                if (
                    "invalid session id"
                    in error_text.lower()
                    or
                    "session deleted"
                    in error_text.lower()
                    or
                    "no such window"
                    in error_text.lower()
                ):

                    print(
                        "🔄 Restarting Chrome session..."
                    )

                    try:

                        driver.quit()

                    except Exception:
                        pass

                    driver = start_browser(
                        crex_url
                    )

                else:

                    time.sleep(
                        5
                    )

    except KeyboardInterrupt:

        print(
            "\n🛑 Bot stopped."
        )

    finally:

        if driver:

            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    main()

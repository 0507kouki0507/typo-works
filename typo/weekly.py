"""Weekly run: let Claude invent phrases, screen them, render the survivors and add them to the catalog.

Every candidate passes four gates in order: the machine phrase check, a rights
check (Claude searches the web and J-PlatPat), the machine image check, and a
visual check (Claude looks at the rendered post). A gate that cannot give a
clear yes counts as a no, with one exception: when the web search is clean but
J-PlatPat could not be read, the work is added on hold, so nothing is posted
until a person has checked the trademark.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from enum import Enum
from functools import partial
from pathlib import Path

from PIL import Image

from typo.catalog import Work, load_catalog, next_no, save_catalog
from typo.checks import MAX_JA_CHARS, MAX_LINE_CHARS, MAX_LINES, check_image, check_phrase, load_banned
from typo.instagram import Instagram, InstagramError
from typo.metrics import record_weekly
from typo.render import LAYOUTS, Target, save_post_jpeg

ClaudeFn = Callable[[str], str]  # prompt -> stdout
Shell = Callable[..., subprocess.CompletedProcess[str]]  # (args, input=None) -> finished process
Refresher = Callable[[str], str]  # current token -> new token
Recorder = Callable[[str, Path, str], object]  # (token, root, today) -> records the weekly numbers

BATCH = 7
AUTO_APPROVE_AFTER = 7  # once the catalog has this many approved works, new ones enter approved

ROOT = Path(__file__).resolve().parent.parent
JST = timezone(timedelta(hours=9))
CLAUDE_TIMEOUT_SECONDS = 600
SHELL_TIMEOUT_SECONDS = 300
TOKEN_KEY = "IG_ACCESS_TOKEN"
USER_ID_KEY = "IG_USER_ID"

log = logging.getLogger(__name__)


class ClaudeReplyError(RuntimeError):
    """Claude's proposal could not be read as the expected JSON, even after one retry."""


class Rights(Enum):
    CLEAN = "clean"  # web search clean and J-PlatPat read with no identical mark
    REJECTED = "rejected"  # a match, a failed search, an unreadable reply, or doubt
    PENDING = "pending"  # web search clean, but J-PlatPat unread (or its "clear" not trusted yet)


TRADEMARK_HOLD = "trademark check pending"
# Until there is a real way to read J-PlatPat, a "clear" from Claude is not trusted either:
# the work is held like an unreadable one. Switch on once J-PlatPat results are genuinely read.
TRUST_JPLATPAT_CLEAR = False
# Names from .env. The claude child process never needs them, so they are removed from its environment.
ENV_SECRETS = ("IG_ACCESS_TOKEN", "IG_USER_ID", "SUZURI_API_KEY")


# --- prompts ------------------------------------------------------------------------------------

LAYOUT_NOTES = {
    "stack": "全行を同じ幅にそろえて大きく積む（基本形）",
    "giant": "1行目だけ画面いっぱいに巨大、残りの行は下に小さく（1行目が短い一語のときに映える）",
    "diagonal": "全体を少し斜めに傾けて積む（動きや勢いのある言葉に）",
    "invert": "黒地に白文字で積む（夜っぽい・静かな言葉に）",
}

PROPOSE_PROMPT = """あなたはタイポグラフィ作品のInstagramアカウントの専属コピーライターです。
作品はInstagramに投稿し、ポスターとステッカーとして販売します。

【テーマ】「がんばりすぎない人の本音」
無理しない、休んでいい、ほどほどでいい、自分のペースでいい。肩の力が抜けた、静かで少し強い本音。
説教・自己啓発の決め台詞・ポエムっぽすぎる言葉は避け、短くて部屋に飾りたくなる言葉にする。

【作品の形】
主役は短いオリジナルの英語フレーズ（大文字で行ごとに大きく積む）。脇に小さな日本語の一行を添える。
モノクロで、1行だけ赤のアクセントを入れられる。
形の例（この例そのものは使わないこと）: phrase "MAKE\\nLESS\\nNOISE" / ja「静かに、強く。」

【言葉のルール】1つでも破った候補は機械的に捨てられます。
- phrase: 英語。1〜{max_lines}行で、行は改行（\\n）で区切る。各行{max_line_chars}文字以内（スペース込み）。空の行は禁止。
- phrase に使える文字は、大文字 A-Z、数字 0-9、スペース、記号 . , ! ? ' & - だけ。小文字・その他の記号・絵文字は禁止。
- ja: 日本語の一行。{max_ja_chars}文字以内（句読点込み）。改行・絵文字・特殊な記号は禁止。phrase の直訳でなくてよい。
- 完全にオリジナルの言葉であること。歌詞、名言・格言、映画・本・曲・番組のタイトル、広告コピー・スローガン、
  ブランド名・商品名・キャラクター名、登録商標と同じ言葉やそっくりな言葉は禁止。有名な言い回しのもじりも禁止。
- 曲名・アルバム名・映画のタイトルになっていそうな短い決まり文句や、Tシャツやグッズでよく見る定番フレーズは避ける。
  誰でも思いつく言い回しではなく、このアカウントにしか書けない言葉にする。
- 下の「既存の作品」と同じ言葉、ほぼ同じ言葉は禁止。候補どうしも重ねない。
- 人を傷つける表現、政治・宗教、下品な言葉は禁止。

【layout】次の4つから選ぶ。
{layouts}

【accent_line】赤くする行の番号（0始まり）。赤を入れないなら -1。phrase の行数の範囲内にする。

【既存の作品】（重複禁止）
{existing}

ツールは使わずに考え、候補を{count}本、次の形のJSONだけで返してください（説明文やコードフェンスは不要）:
{{"candidates": [{{"phrase": "LINE ONE\\nLINE TWO", "ja": "日本語の一行", "layout": "stack", "accent_line": 1}}]}}"""

RIGHTS_PROMPT = """あなたは商品化前の権利チェック担当です。次の英語フレーズを、ポスターとステッカーに印刷して販売してよいか調べてください。

フレーズ: 「{phrase}」
（作品では行を分けて組みますが、ここでは1つの文として扱ってください）

記憶だけで判断してはいけません。必ず実際に次の2つを調べてください。
1. Web検索: WebSearch でフレーズを完全一致（"{phrase}" のように引用符で囲む）で検索し、
   歌詞、曲・アルバム・本・映画・番組のタイトル、名言・格言、広告コピー・スローガン、ブランド名・商品名・キャラクター名として
   使われていないか確認する。
2. 商標: J-PlatPat（https://www.j-platpat.inpit.go.jp/ 特許庁の特許情報プラットフォーム）の商標検索で、
   フレーズ全体と同じ文字列の登録商標・出願がないか確認する。
   J-PlatPat はJavaScriptで動くため、開けない・結果を読めないことがあります。そのときは推測で埋めずに
   jplatpat を "unreadable" にしてください。ほかのサイトの結果で J-PlatPat の代わりにしてはいけません。

判定のしかた:
- Web検索で、上のどれかと同じ、またはほぼ同じ（語順違い、1語だけ違う、単数・複数や綴りの違い、明らかなもじり）が見つかったら ok: false。
- 商標: フレーズ全体と同じ商標が登録済み、または出願中なら ok: false。
  フレーズの中のありふれた単語を1つ含むだけの商標は、不合格の理由にしない。
- Web検索ができなかった、結果を確認しきれなかったときは ok: false（調べられなかったものは不合格）。
- 迷ったら ok: false。
- ただし J-PlatPat を読めなかったことだけを理由に ok: false にしてはいけません。そのときは jplatpat を "unreadable" にし、
  ok は Web検索の結果だけで決めてください。"unreadable" の作品は投稿されず、あとで人が J-PlatPat を確認します。

jplatpat には次のどれかを入れてください:
- "clear": J-PlatPat の検索結果を実際に見て、フレーズ全体と同じ商標がなかった
- "found": フレーズ全体と同じ商標があった（このときは ok: false）
- "unreadable": J-PlatPat を開けなかった、または検索結果を読めなかった

最後に、次の形のJSONだけを返してください（説明文やコードフェンスは不要）:
{{"ok": false, "jplatpat": "unreadable", "reason": "Web検索で何を調べて何が見つかったか、J-PlatPat で見えたこと（件数、近い商標があればその区分・権利者）を1〜3文で"}}"""

LOOKS_PROMPT = """次の画像ファイルを Read ツールで開いて、目で確認してください。
{path}

これはInstagram投稿用のタイポグラフィ作品（1080×1350）で、同じデザインを印刷物としても販売します。厳しめに見てください。
{expected}
確認すること:
- 書かれている文字がすべて、1文字も欠けずにはっきり読めるか（文字化け、□の豆腐、欠けた文字がないか）
- 文字が画像の端で切れていないか、はみ出していないか、文字どうしや線と重なっていないか
- 意図したデザインに見えるか（読めないほど小さい行、崩れた配置がないか）

デザインの仕様なので、次のことは問題にしないでください:
- 英語の行ごとに幅が違うこと（短い行は大きくなりすぎないよう抑えてあり、右側が空くことがある）
- 英語のかたまりの下や周りに余白があること（かたまりは上に寄せて置き、右下の日本語と線との間は空く）

1つでも問題があれば ok: false。画像を開けなかったときも ok: false。
次の形のJSONだけを返してください（説明文やコードフェンスは不要）:
{{"ok": false, "reason": "見たことと判断の理由を1〜2文で"}}"""


def _existing_list(works: list[Work]) -> str:
    lines = [f"- {' '.join(w.phrase.split())}（{w.ja}）" for w in works]
    return "\n".join(lines) or "（まだありません）"


def propose_prompt(works: list[Work], n: int) -> str:
    layouts = "\n".join(f"- {name}: {LAYOUT_NOTES[name]}" for name in LAYOUTS)
    return PROPOSE_PROMPT.format(
        max_lines=MAX_LINES,
        max_line_chars=MAX_LINE_CHARS,
        max_ja_chars=MAX_JA_CHARS,
        layouts=layouts,
        existing=_existing_list(works),
        count=n,
    )


def rights_prompt(phrase: str) -> str:
    return RIGHTS_PROMPT.format(phrase=" ".join(phrase.split()))


def _expected_text(work: Work | None) -> str:
    if work is None:
        return ""
    lines = " / ".join(line.strip() for line in work.phrase.split("\n"))
    return (
        "画像に書かれているはずの文字:\n"
        f"- 英語（大きな文字）: {lines}\n"
        f"- 日本語（右下の小さな一行）: {work.ja}\n"
        f"- 作品番号（左下）: NO.{work.no:03d}\n"
    )


def looks_prompt(image_path: Path, work: Work | None = None) -> str:
    return LOOKS_PROMPT.format(path=image_path.resolve(), expected=_expected_text(work))


# --- reading Claude's replies -------------------------------------------------------------------


def _json_object(text: str) -> dict:
    """The outermost {...} in the reply. Raises ValueError if there is none or it is not an object."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in reply")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("reply is not a JSON object")
    return data


def _candidate(item: object) -> dict | None:
    """A well-formed candidate, or None. Word rules are left to check_phrase."""
    if not isinstance(item, dict):
        return None
    phrase, ja, layout, accent = (item.get(k) for k in ("phrase", "ja", "layout", "accent_line"))
    if not (isinstance(phrase, str) and isinstance(ja, str) and layout in LAYOUTS):
        return None
    if isinstance(accent, bool) or not isinstance(accent, int):
        return None
    if not -1 <= accent < len(phrase.split("\n")):
        return None
    return {"phrase": phrase, "ja": ja, "layout": layout, "accent_line": accent}


def _candidates(text: str) -> list[dict]:
    items = _json_object(text).get("candidates")
    if not isinstance(items, list):
        raise ValueError("candidates is not a list")
    parsed = [_candidate(item) for item in items]
    dropped = parsed.count(None)
    if dropped:
        log.info("skipped %d malformed candidate(s)", dropped)
    return [c for c in parsed if c is not None]


def _verdict(text: str) -> tuple[bool, str]:
    """(ok, reason). ok is True only for a literal JSON true."""
    data = _json_object(text)
    return data.get("ok") is True, str(data.get("reason", ""))


# --- the three Claude steps ---------------------------------------------------------------------


def propose_phrases(run: ClaudeFn, works: list[Work], n: int) -> list[dict]:
    """Ask for n candidates. An unreadable reply is retried once, then ClaudeReplyError."""
    prompt = propose_prompt(works, n)
    problem = ""
    for _ in range(2):
        try:
            return _candidates(run(prompt))
        except ValueError as error:  # json.JSONDecodeError is a ValueError
            problem = str(error)
            log.warning("proposal reply unreadable: %s", problem)
    raise ClaudeReplyError(f"proposal reply unreadable twice: {problem}")


def _ask_verdict(run: ClaudeFn, prompt: str, label: str) -> tuple[bool, str]:
    """A check that cannot be performed or read is a failure, never a pass."""
    try:
        return _verdict(run(prompt))
    except ValueError as error:
        return False, f"{label} reply unreadable: {error}"
    except Exception as error:  # the check could not run at all
        return False, f"{label} could not run: {type(error).__name__}"


def _rights(text: str) -> tuple[Rights, str]:
    data = _json_object(text)
    reason = str(data.get("reason", ""))
    if data.get("ok") is not True:
        return Rights.REJECTED, reason
    jplatpat = data.get("jplatpat")
    if jplatpat == "clear":
        return (Rights.CLEAN if TRUST_JPLATPAT_CLEAR else Rights.PENDING), reason
    if jplatpat == "unreadable":
        return Rights.PENDING, reason
    return Rights.REJECTED, f"unexpected jplatpat value {jplatpat!r}: {reason}"


def rights_check(run: ClaudeFn, phrase: str) -> tuple[Rights, str]:
    """Clean, rejected, or pending (web clean, J-PlatPat unread). Anything unclear is rejected."""
    try:
        return _rights(run(rights_prompt(phrase)))
    except ValueError as error:
        return Rights.REJECTED, f"rights check reply unreadable: {error}"
    except Exception as error:  # the check could not run at all
        return Rights.REJECTED, f"rights check could not run: {type(error).__name__}"


def rights_ok(run: ClaudeFn, phrase: str) -> bool:
    """True only when both the web search and J-PlatPat were clean."""
    return rights_check(run, phrase)[0] is Rights.CLEAN


def looks_verdict(run: ClaudeFn, image_path: Path, work: Work | None = None) -> tuple[bool, str]:
    return _ask_verdict(run, looks_prompt(image_path, work), "visual check")


def looks_ok(run: ClaudeFn, image_path: Path, work: Work | None = None) -> bool:
    """Visual check. Given the work, Claude also verifies every expected word is there."""
    return looks_verdict(run, image_path, work)[0]


# --- the batch ----------------------------------------------------------------------------------


def make_batch(run: ClaudeFn, catalog_path: Path, works_dir: Path, today: str, n: int = BATCH) -> list[Work]:
    """Add at most n screened works, held ones included.

    Works whose trademark check is pending are added on hold. Fewer than n is
    fine; the batch is never padded. Each work is in the catalog only once its
    post.jpg is on disk, and the catalog is saved after every addition.
    """
    works = load_catalog(catalog_path)
    banned = load_banned()
    approved = sum(1 for w in works if w.approved) >= AUTO_APPROVE_AFTER
    added: list[Work] = []
    for item in propose_phrases(run, works, n * 2):
        if len(added) >= n:
            break
        work = Work(no=next_no(works), created=today, approved=approved, **item)
        if _accepted(run, work, works, banned, works_dir):
            works.append(work)
            added.append(work)
            save_catalog(catalog_path, works)
    return added


def _accepted(run: ClaudeFn, work: Work, works: list[Work], banned: list[str], works_dir: Path) -> bool:
    name = " ".join(work.phrase.split())
    problems = check_phrase(work.phrase, work.ja, works, banned)
    if problems:
        log.info("dropped %r at phrase check: %s", name, "; ".join(problems))
        return False
    rights, reason = rights_check(run, work.phrase)
    if rights is Rights.REJECTED:
        log.info("dropped %r at rights check: %s", name, reason)
        return False
    if rights is Rights.PENDING:
        work.hold = TRADEMARK_HOLD
    log.info("rights %s %r: %s", rights.value, name, reason)
    image_path = works_dir / f"{work.no:03d}" / "post.jpg"
    ok, reason = _rendered_and_looked_at(run, work, image_path)
    if not ok:
        _remove_post(image_path)
        log.info("dropped %r at %s", name, reason)
        return False
    held = f" on hold ({work.hold})" if work.hold else ""
    log.info("added NO.%03d %r%s (%s)", work.no, name, held, reason)
    return True


def _rendered_and_looked_at(run: ClaudeFn, work: Work, image_path: Path) -> tuple[bool, str]:
    image_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        save_post_jpeg(work, image_path)
    except ValueError as error:  # e.g. a phrase that cannot be fitted
        return False, f"render: {error}"
    with Image.open(image_path) as img:
        problems = check_image(img, Target.POST)
    if problems:
        return False, f"image check: {'; '.join(problems)}"
    ok, reason = looks_verdict(run, image_path, work)
    return ok, f"visual check: {reason}"


def _remove_post(image_path: Path) -> None:
    """Remove the rejected image, and its folder if nothing else is in it."""
    image_path.unlink(missing_ok=True)
    folder = image_path.parent
    if folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()


# --- production Claude --------------------------------------------------------------------------


def _claude_bin() -> str:
    return shutil.which("claude") or str(Path.home() / ".local/bin/claude")


SYSTEM_PROMPT = "指示された作業をしたうえで、最後は指定されたJSONだけを返してください。説明文やコードフェンスは不要です。"

def _claude_env() -> dict[str, str]:
    """This process's environment without the .env secrets (launchd sources .env before running us)."""
    return {name: value for name, value in os.environ.items() if name not in ENV_SECRETS}


def _claude(args: list[str], prompt: str, cwd: Path) -> str:
    """Run headless `claude -p` the way zenn-publisher does; anything needing a permission prompt is denied."""
    proc = subprocess.run(
        [
            _claude_bin(), "-p",
            "--model", "sonnet",
            "--system-prompt", SYSTEM_PROMPT,
            *args,
            "--permission-prompts", "none",
            "--setting-sources", "",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--no-session-persistence",
        ],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=CLAUDE_TIMEOUT_SECONDS,
        cwd=cwd,
        env=_claude_env(),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p failed rc={proc.returncode}: {proc.stdout[:200]} {proc.stderr[:200]}")
    return proc.stdout


def claude_cli(workdir: Path) -> ClaudeFn:
    """For proposals and the visual check. Runs inside `workdir` (the works folder), so Read
    reaches the rendered images and nothing else; Read is never pre-approved."""
    args = ["--tools", "WebSearch,WebFetch,Read", "--allowedTools", "WebSearch,WebFetch"]
    return lambda prompt: _claude(args, prompt, workdir)


# --- main: commit, push, refresh the token ------------------------------------------------------


def _shell(args: list[str], input: str | None = None, *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, input=input, capture_output=True, text=True, cwd=cwd, timeout=SHELL_TIMEOUT_SECONDS)


def _user_id(env_path: Path | None = None) -> str:
    """IG_USER_ID: the environment wins; otherwise .env, the way the token is read. Empty if absent."""
    return os.environ.get(USER_ID_KEY) or _env_value(env_path or ROOT / ".env", USER_ID_KEY) or ""


def _instagram_refresh(token: str) -> str:
    return Instagram(_user_id(), token).refresh_token()


def _instagram_record(token: str, root: Path, today: str) -> object:
    ig = Instagram(_user_id(root / ".env"), token)
    return record_weekly(ig, load_catalog(root / "works" / "catalog.json"), root / "metrics" / "weekly.csv", today)


def _today() -> str:
    return datetime.now(JST).date().isoformat()


def _failed(shell: Shell, args: list[str], input: str | None = None) -> str | None:
    """Run a command; return an error line (never including `input`) or None."""
    proc = shell(args, input=input)
    if proc.returncode == 0:
        return None
    detail = (proc.stderr or proc.stdout or "").strip().splitlines()
    return f"{' '.join(args[:3])} failed (exit {proc.returncode}): {detail[-1] if detail else ''}".rstrip(": ")


def sync_with_remote(shell: Shell) -> str | None:
    """Fast-forward to the remote before making anything; the daily job pushes catalog changes."""
    return _failed(shell, ["git", "pull", "--ff-only"])


def _push(shell: Shell) -> str | None:
    """Push; if rejected, rebase onto the remote once and push again. A failed rebase is aborted."""
    if not _failed(shell, ["git", "push"]):
        return None
    if error := _failed(shell, ["git", "pull", "--rebase", "--autostash"]):
        shell(["git", "rebase", "--abort"])
        return f"push rejected and rebase failed: {error}"
    return _failed(shell, ["git", "push"])


def publish_batch(shell: Shell, added: list[Work]) -> str | None:
    """Stage and commit only what the weekly job made (explicit paths, so nothing else already
    staged in this checkout can slip in), then push. Returns an error line or None."""
    if not added:
        return None
    paths = ["works/catalog.json", *[f"works/{w.no:03d}" for w in added]]
    message = f"feat: add works NO.{added[0].no:03d}-NO.{added[-1].no:03d}"
    for args in (["git", "add", "--", *paths], ["git", "commit", "-m", message, "--", *paths]):
        if error := _failed(shell, args):
            return error
    return _push(shell)


def _env_value(env_path: Path, key: str) -> str | None:
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip() or None
    return None


def _read_token(env_path: Path) -> str:
    if value := _env_value(env_path, TOKEN_KEY):
        return value
    raise ValueError(f"{TOKEN_KEY} is missing or empty in .env")


def _write_token(env_path: Path, token: str) -> None:
    """Replace only the token line; the file keeps its permissions. Written via a temp file, then swapped in."""
    lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
    replaced = [f"{TOKEN_KEY}={token}\n" if line.startswith(f"{TOKEN_KEY}=") else line for line in lines]
    mode = os.stat(env_path).st_mode & 0o7777
    fd, tmp = tempfile.mkstemp(dir=env_path.parent, prefix=".env.")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.writelines(replaced)
        os.replace(tmp, env_path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def refresh_token(shell: Shell, refresh: Refresher, env_path: Path) -> str | None:
    """Refresh the Instagram token, write it to .env and GitHub Secrets. Returns an error line or None.

    The token goes to gh on stdin and never into a message. On failure the old token stays.
    """
    try:
        new = refresh(_read_token(env_path))
    except InstagramError as error:  # its message is built never to contain the token
        return f"token refresh failed: {error}"
    except Exception as error:
        return f"token refresh failed: {type(error).__name__}"
    try:
        _write_token(env_path, new)
    except OSError as error:
        return f"token refresh: writing .env failed: {type(error).__name__}"
    error = _failed(shell, ["gh", "secret", "set", TOKEN_KEY], input=new)
    return error.replace(new, "***") if error else None


def _run_batch(run: ClaudeFn, works_dir: Path, today: str) -> tuple[list[Work], str | None]:
    """make_batch, but works it added before an exception are still returned, so they get committed."""
    catalog_path = works_dir / "catalog.json"
    before = {w.no for w in load_catalog(catalog_path)}
    error = None
    try:
        make_batch(run, catalog_path, works_dir, today)
    except Exception as exc:
        error = f"batch failed: {type(exc).__name__}: {exc}"
    added = [w for w in load_catalog(catalog_path) if w.no not in before]
    return added, error


def main(
    *,
    root: Path = ROOT,
    run: ClaudeFn | None = None,
    shell: Shell | None = None,
    refresh: Refresher = _instagram_refresh,
    record: Recorder = _instagram_record,
    today: str | None = None,
) -> int:
    """git pull, make_batch, git add/commit/push, then the token refresh, then the weekly numbers.

    A failed pull skips the batch; everything else is attempted regardless; any failure → 1.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    works_dir = root / "works"
    day = today or _today()
    shell = shell or partial(_shell, cwd=root)
    errors: list[str] = []
    if error := sync_with_remote(shell):
        errors.append(f"{error}; batch skipped (nothing was made)")
    else:
        try:
            added, batch_error = _run_batch(run or claude_cli(works_dir), works_dir, day)
            held = sum(1 for w in added if w.hold)
            print(f"added {len(added)} work(s), {held} on hold")
            errors += [e for e in (batch_error, publish_batch(shell, added)) if e]
        except Exception as error:  # the catalog itself could not be read
            errors.append(f"batch failed: {type(error).__name__}: {error}")
    if error := refresh_token(shell, refresh, root / ".env"):
        errors.append(error)
    try:  # after the refresh, so it uses the newest token
        record(_read_token(root / ".env"), root, day)
    except Exception as error:
        errors.append(f"weekly numbers failed: {type(error).__name__}: {error}")
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

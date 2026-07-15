#!/usr/bin/env python3
"""Claude Code PreToolUse hook (matcher: Bash) — ДЕТЕКТ удаления → запрос y/n.

В отличие от block-destructive.py (тот делал exit 2 = глухой блок), этот хук на
деструктивной команде возвращает permissionDecision:"ask" → Claude Code показывает
интерактивное подтверждение (y/n) ДАЖЕ в bypassPermissions (explicit ask переживает
bypass). На обычных командах — молча пропускает (exit 0, без решения).

Severity-тиринг для bypass: каждый детект помечается уровнем — CRITICAL
(необратимо или бьёт за пределы рабочей копии) или ORDINARY (локальное удаление
файлов, обратимое). В режиме permission_mode=="bypassPermissions" хук поднимает
y/n ТОЛЬКО на CRITICAL, ORDINARY проходит молча (раз пользователь явно ушёл в
bypass). Во всех остальных режимах — ask на любой детект, как раньше.

CRITICAL: rm -r/-rf (кроме регенерируемых ./build|dist|node_modules…); rm по
/, ~, *, абс./системным путям; shred/srm; dropdb; dd на /dev/…; mkfs/wipefs/
blkdiscard; find -delete/-exec rm; git reset --hard|clean -f|push --force|
branch -D|filter-branch|stash clear|update-ref -d; docker prune|volume rm|
compose down -v; kubectl delete; terraform/tofu/pulumi destroy; redis FLUSHALL/
FLUSHDB; aws rb|delete-*|terminate-*|s3 rm --recursive; gcloud/az … delete;
crontab -r; SQL DROP/TRUNCATE/DELETE FROM + mongo dropDatabase/deleteMany.
ORDINARY: rm <file> без -r; rmdir|unlink|truncate; git rm; docker rm/rmi,
(image|network|container) rm; dd of=<файл>; aws s3 rm <один-объект>.

Детект — по токенам (split по ; && || | & ( ) `, первый токен каждой простой
команды), поэтому `perform`/`transform`/`terraform` НЕ ловятся.

Ловит: rm rmdir shred unlink srm truncate dropdb; find -delete/-exec rm|unlink|
shred|srm|rmdir; git rm|clean|reset --hard|push --force/-f|branch -D; docker rm/
rmi, (volume|image|system|network|container|builder) prune, (volume|image|network|
container) rm, compose down -v; docker-compose down -v; SQL DROP/TRUNCATE/DELETE
FROM при наличии db-клиента.

Закрытые обходы (см. живой red-team):
- `\\rm`, `\\git` — снимаем ведущий backslash-эскейп с имени программы;
- `git -C path reset`, `git -c k=v rm`, `git --git-dir=… reset` — пропускаем
  глобальные опции git перед сабкомандой;
- `docker --context prod rm`, `docker -H host rmi` — то же для docker;
- `bash -c "rm …"`, `sh -c '…'`, `bash -lc "…"` — рекурсивно разбираем строку -c;
- `… | xargs rm`, `xargs -0 rm` — разбираем команду, которую запускает xargs.

ВНЕ охвата Bash-слоя (деструктив внутри файла/скрипта, токен-парсер его не видит):
`python x.py`/`os.remove`, `node x.js`/`unlinkSync`, `psql -f file.sql`, `bash x.sh`.
Их частично прикрывает встроенный guard Claude Code. НЕ ловим, чтобы не флажить
любой запуск интерпретатора (это убило бы signal-to-noise гарда).

НЕ трогает: '>' редиректы, docker compose down без -v, git push --force-with-lease.

Дополнительно (позаимствовано у Dicklesworthstone/destructive_command_guard):
- here-string `sh -c`-класса: `bash <<< "rm -rf /"` — извлекаем строку после `<<<`
  и разбираем рекурсивно (многострочный heredoc-body уже ловится сплитом по '\\n');
- fail-open + внутренний таймаут: detect() обёрнут в SIGALRM-лимит (NDG_TIMEOUT_MS,
  дефолт 200мс); если разбор упал/завис — молча пропускаем (proceed), чтобы хук не
  становился бутылочным горлышком. NDG_FAIL_CLOSED=1 разворачивает: непонятная
  команда → ask;
- CLI dry-run: `python3 confirm-destructive.py --test [--mode M] "<команда>"`
  печатает решение без исполнения хука; код выхода 0=proceed, 1=ask.
"""
import os
import sys
import json
import re
import shutil
import subprocess

# Звук/уведомление при детекте можно отключить: NDG_NOTIFY=0
_NOTIFY = os.environ.get("NDG_NOTIFY", "1") != "0"
# Имя системного звука macOS (Funk/Sosumi/Basso/Glass/Ping/Hero...). NDG_SOUND.
_SOUND = os.environ.get("NDG_SOUND", "Funk")
_TITLE = "🛡️ Команда на удаление"
# Bundle-id терминала-хоста можно задать явно: NDG_TERM_BUNDLE.
# Клик по баннеру возвращает в этот терминал — но только если установлен
# terminal-notifier (`brew install terminal-notifier`). Без него — osascript-
# фолбэк: баннер есть, но клик открывает Script Editor, а не терминал.
_TERM_BUNDLES = {
    "iTerm.app": "com.googlecode.iterm2",
    "Apple_Terminal": "com.apple.Terminal",
    "vscode": "com.microsoft.VSCode",
    "WezTerm": "com.github.wez.wezterm",
    "Hyper": "co.zeit.hyper",
    "ghostty": "com.mitchellh.ghostty",
    "Tabby": "org.tabby",
    "kitty": "net.kovidgoyal.kitty",
    "WarpTerminal": "dev.warp.Warp-Stable",
}

# Внутренний лимит на разбор одной команды (мс). Превышение → fail-open (proceed),
# если только не NDG_FAIL_CLOSED=1.
_TIMEOUT_S = max(0.0, float(os.environ.get("NDG_TIMEOUT_MS", "200"))) / 1000.0
_FAIL_CLOSED = os.environ.get("NDG_FAIL_CLOSED", "0") == "1"

DB_CLIENTS = r'\b(psql|mysql|mariadb|mongosh|mongo|clickhouse-client|clickhouse|sqlite3|dropdb)\b'
PREFIXES = {"sudo", "command", "time", "env", "nohup", "builtin", "exec",
            "then", "do", "else", "{", "(", "!"}
DESTRUCTIVE = {"rm", "rmdir", "shred", "unlink", "srm", "truncate", "dropdb"}
SHELLS = {"sh", "bash", "zsh", "dash", "ash", "ksh", "fish"}

# Уровни критичности (для bypass-тиринга, см. docstring).
CRIT = "critical"
ORD = "ordinary"
# Режимы Claude Code, где Bash выполняется без промпта → ослабляем фильтр до CRIT.
RELAXED_MODES = {"bypassPermissions"}
# Регенерируемые локальные каталоги: рекурсивный rm по ним — ORDINARY.
REGEN_DIRS = {"build", "dist", "node_modules", ".next", "target", "coverage",
              "out", ".cache", "tmp", ".pytest_cache", "__pycache__", ".turbo",
              ".parcel-cache", ".nuxt", ".svelte-kit", ".gradle", "bin", "obj"}

# Глобальные опции, забирающие отдельный токен-значение (форма `--opt val`).
# Форма `--opt=val` распознаётся отдельно (по наличию '=').
GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace",
                  "--super-prefix", "--exec-path"}
DOCKER_VALUE_OPTS = {"--context", "-H", "--host", "--config", "--log-level",
                     "-l", "--tlscacert", "--tlscert", "--tlskey"}
XARGS_VALUE_OPTS = {"-I", "-i", "-n", "-P", "-d", "-s", "-a", "-E", "-L",
                    "--max-procs", "--max-args", "--delimiter", "--arg-file",
                    "--max-lines", "--replace", "--max-chars"}
# Ведущие глобальные опции kubectl (форма `--opt val`), забирающие токен-значение.
KUBECTL_VALUE_OPTS = {"-n", "--namespace", "--context", "--cluster", "-s",
                      "--server", "--kubeconfig", "--user", "--as", "--as-group",
                      "--token", "-l", "--selector", "--field-selector"}
# Форматирование дисков / затирание — всегда CRITICAL.
DISK_WIPE = {"wipefs", "blkdiscard", "shred"}  # shred уже в DESTRUCTIVE, тут для mkfs-ветки


def _term_bundle_id():
    """Bundle-id хост-приложения терминала (для иконки уведомления и фокуса по
    клику). Приоритет: NDG_TERM_BUNDLE > __CFBundleIdentifier > карта TERM_PROGRAM."""
    b = os.environ.get("NDG_TERM_BUNDLE") or os.environ.get("__CFBundleIdentifier")
    if b:
        return b
    return _TERM_BUNDLES.get(os.environ.get("TERM_PROGRAM", ""))


def _alert(reason: str):
    """Звук + баннер-уведомление (macOS) + терминальный BEL. Fire-and-forget,
    stdout НЕ трогаем (там только JSON-решение хука).

    Если установлен terminal-notifier — уведомление постится от имени терминала
    (`-sender <bundle>`): правильная иконка + клик возвращает в терминал. Иначе —
    osascript-фолбэк (баннер есть, но клик открывает Script Editor)."""
    if not _NOTIFY:
        return
    # BEL в stderr — если в терминале включён visual bell, экран мигнёт
    try:
        sys.stderr.write("\a")
        sys.stderr.flush()
    except Exception:
        pass
    if sys.platform != "darwin":
        return
    safe = reason.replace('"', "'")[:180]
    dn = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    try:
        tn = shutil.which("terminal-notifier")
        bundle = _term_bundle_id()
        if tn and bundle:
            # от имени терминала: иконка терминала + клик фокусит его
            subprocess.Popen(
                [tn, "-title", _TITLE, "-message", safe,
                 "-sound", _SOUND, "-sender", bundle], **dn)
            return
        # фолбэк: display notification = баннер + звук одним вызовом
        subprocess.Popen(
            ["osascript", "-e",
             f'display notification "{safe}" with title "{_TITLE}" sound name "{_SOUND}"'],
            **dn)
        # дублируем звук afplay (на случай если уведомления приглушены)
        snd = f"/System/Library/Sounds/{_SOUND}.aiff"
        if os.path.exists(snd):
            subprocess.Popen(["afplay", snd], **dn)
    except Exception:
        pass


def proceed():
    # никакого решения — дальше решают permission-правила (в bypass = выполнить)
    sys.exit(0)


def ask(reason: str):
    _alert(reason)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": f"⚠️ Команда на удаление — подтвердите: {reason}",
        }
    }))
    sys.exit(0)


def _strip_opts(args, value_opts):
    """Срезать ведущие опции (всё, что начинается с '-'), вернуть остаток —
    первый позиционный аргумент (сабкоманда / запускаемая программа) и далее.
    Опции из value_opts забирают следующий токен как своё значение."""
    j = 0
    while j < len(args) and args[j].startswith("-"):
        a = args[j]
        if "=" in a:
            j += 1
        elif a in value_opts:
            j += 2
        else:
            j += 1
    return args[j:]


def _unquote(s: str) -> str:
    s = s.strip()
    for q in ('"', "'"):
        if len(s) >= 2 and s[0] == q and s[-1] == q:
            return s[1:-1]
    # частичная кавычка (top-level split мог разорвать строку) — снимаем края
    return s.strip('"').strip("'")


def _norm_target(t: str) -> str:
    t = t.rstrip("/")
    if t.startswith("./"):
        t = t[2:]
    return t


def _rm_severity(args) -> str:
    """CRIT/ORD для `rm`: рекурсия или опасная цель → critical; одиночные файлы
    и регенерируемые ./build|dist|node_modules… → ordinary."""
    flags = [a for a in args if a.startswith("-")]
    targets = [a for a in args if not a.startswith("-")]
    for t in targets:
        tt = t.rstrip("/")
        if (tt in ("", "/", "~", "*", ".", "..")
                or tt.startswith(("/", "~", "$"))
                or "*" in tt or ".." in tt):
            return CRIT
    recursive = (any("r" in f.lower() for f in flags if not f.startswith("--"))
                 or "--recursive" in flags)
    if recursive:
        norm = [_norm_target(t) for t in targets]
        if targets and all(n in REGEN_DIRS for n in norm):
            return ORD
        return CRIT
    return ORD


def _dd_severity(args):
    """`dd`: цель of= на устройстве/абс.пути → CRITICAL; иначе (перезапись файла в
    cwd) → ORDINARY. Без of= — не деструктив (чтение)."""
    for a in args:
        if a.startswith("of="):
            val = a[3:].strip("'\"")
            if (val.startswith(("/dev/", "/", "~", "$"))
                    or "disk" in val or "rdisk" in val):
                return CRIT
            return ORD
    return None


def detect(cmd: str, _depth: int = 0):
    """Вернуть (severity, reason) если команда деструктивная, иначе None."""
    if _depth > 4 or not cmd.strip():
        return None

    low = cmd.lower()
    if re.search(DB_CLIENTS, low):
        if (re.search(r'\bdrop\s+(table|database|schema)\b', low)
                or re.search(r'\btruncate\b', low)
                or re.search(r'\bdelete\s+from\b', low)):
            return (CRIT, f"SQL-удаление данных: {cmd.strip()}")
        # mongo: dropDatabase()/.drop()/deleteMany()
        if re.search(r'dropdatabase\s*\(|\.drop\s*\(|deletemany\s*\(', low):
            return (CRIT, f"NoSQL-удаление данных: {cmd.strip()}")

    segments = re.split(r'\|\||&&|\$\(|[;\n|&()`]', cmd)
    for seg in segments:
        s = seg.strip()
        if not s:
            continue
        toks = s.split()
        i = 0
        while i < len(toks):
            t = toks[i]
            if re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', t):
                i += 1
                continue
            if t.split("/")[-1].lstrip("\\") in PREFIXES:
                i += 1
                continue
            break
        if i >= len(toks):
            continue
        # имя программы: убрать путь и снять ведущий backslash-эскейп (`\rm` → `rm`)
        prog = toks[i].split("/")[-1].lstrip("\\")
        args = toks[i + 1:]

        if prog in DESTRUCTIVE:
            if prog == "rm":
                return (_rm_severity(args), f"удаление: {s}")
            if prog in ("shred", "srm", "dropdb"):
                return (CRIT, f"удаление: {s}")
            return (ORD, f"удаление: {s}")  # rmdir, unlink, truncate

        if prog == "find" and re.search(
                r'(^|\s)(-delete\b|-(exec|execdir|ok|okdir)\s+(rm|unlink|shred|srm|rmdir)\b)',
                " " + " ".join(args)):
            return (CRIT, f"find-удаление: {s}")

        if prog == "git" and args:
            ga = _strip_opts(args, GIT_VALUE_OPTS)
            if ga:
                head = ga[0]
                rest = ga[1:]
                if head == "rm":
                    return (ORD, f"git rm: {s}")
                if head == "clean":
                    sev = CRIT if any("f" in f for f in rest if f.startswith("-")
                                      and not f.startswith("--")) or "--force" in rest else ORD
                    return (sev, f"git clean: {s}")
                if head == "reset" and "--hard" in rest:
                    return (CRIT, f"git reset --hard: {s}")
                if head == "push" and any(
                        a in ("--force", "-f") or a.startswith("+") for a in rest):
                    return (CRIT, f"git push --force: {s}")
                if head == "branch" and "-D" in rest:
                    return (CRIT, f"git branch -D: {s}")
                if head == "filter-branch":
                    return (CRIT, f"git filter-branch: {s}")
                if head == "stash" and "clear" in rest:
                    return (CRIT, f"git stash clear: {s}")
                if head == "update-ref" and "-d" in rest:
                    return (CRIT, f"git update-ref -d: {s}")

        if prog in ("docker", "podman") and args:
            da = _strip_opts(args, DOCKER_VALUE_OPTS)
            if da:
                if da[0] in ("rm", "rmi"):
                    return (ORD, f"docker {da[0]}: {s}")
                if len(da) >= 2 and da[1] == "prune" and da[0] in (
                        "volume", "image", "system", "network", "container", "builder"):
                    return (CRIT, f"docker {da[0]} prune: {s}")
                if len(da) >= 2 and da[0] in (
                        "volume", "image", "network", "container") and da[1] == "rm":
                    sev = CRIT if da[0] == "volume" else ORD
                    return (sev, f"docker {da[0]} rm: {s}")
                if da[0] == "compose" and "down" in da and any(
                        a in ("-v", "--volumes") for a in da):
                    return (CRIT, f"docker compose down -v: {s}")

        if prog == "docker-compose" and "down" in args and any(
                a in ("-v", "--volumes") for a in args):
            return (CRIT, f"docker-compose down -v: {s}")

        # dd на устройство/абс.путь — CRITICAL; перезапись файла в cwd — ORDINARY
        if prog == "dd":
            sev = _dd_severity(args)
            if sev:
                return (sev, f"dd перезапись: {s}")

        # форматирование ФС / затирание диска
        if prog == "mkfs" or prog.startswith("mkfs.") or prog in DISK_WIPE:
            return (CRIT, f"форматирование/затирание: {s}")

        # kubectl delete <resource>
        if prog == "kubectl" and args:
            ka = _strip_opts(args, KUBECTL_VALUE_OPTS)
            if ka and ka[0] == "delete":
                return (CRIT, f"kubectl delete: {s}")

        # terraform/tofu/terragrunt destroy | apply -destroy
        if prog in ("terraform", "tofu", "terragrunt") and args:
            ta = _strip_opts(args, set())
            if ta and (ta[0] == "destroy"
                       or (ta[0] == "apply" and "-destroy" in ta[1:])):
                return (CRIT, f"{prog} destroy: {s}")

        # pulumi destroy
        if prog == "pulumi" and "destroy" in args:
            return (CRIT, f"pulumi destroy: {s}")

        # redis/valkey FLUSHALL/FLUSHDB
        if prog in ("redis-cli", "valkey-cli") and any(
                a.upper() in ("FLUSHALL", "FLUSHDB") for a in args):
            return (CRIT, f"redis flush: {s}")

        # aws: rb / delete-*/terminate-*/remove-* / s3 rm
        if prog == "aws" and args:
            if "rb" in args or any(
                    re.match(r'^(delete|terminate|remove)-\w', a) for a in args):
                return (CRIT, f"aws destructive: {s}")
            if "rm" in args:
                sev = CRIT if "--recursive" in args else ORD
                return (sev, f"aws s3 rm: {s}")

        # gcloud/az ... delete
        if prog in ("gcloud", "az") and "delete" in args:
            return (CRIT, f"{prog} delete: {s}")

        # crontab -r (снести все задания пользователя)
        if prog == "crontab" and any(
                a.startswith("-") and "r" in a for a in args):
            return (CRIT, f"crontab -r: {s}")

        # bash/sh -c "…": разобрать строку-скрипт рекурсивно
        if prog in SHELLS and args:
            ci = None
            for k, a in enumerate(args):
                if a == "-c" or re.match(r'^-[A-Za-z]*c$', a):
                    ci = k
                    break
            if ci is not None and ci + 1 < len(args):
                inner = _unquote(" ".join(args[ci + 1:]))
                r = detect(inner, _depth + 1)
                if r:
                    return r
            # here-string: `bash <<< "rm -rf /"` — разобрать строку после <<<
            m = re.search(r'<<<\s*(.+)$', s)
            if m:
                r = detect(_unquote(m.group(1)), _depth + 1)
                if r:
                    return r

        # xargs CMD: разобрать команду, которую запускает xargs
        if prog == "xargs" and args:
            rest = _strip_opts(args, XARGS_VALUE_OPTS)
            if rest:
                r = detect(" ".join(rest), _depth + 1)
                if r:
                    return r

    return None


class _Timeout(Exception):
    pass


def _safe_detect(cmd: str):
    """detect() под таймаутом. При зависании/ошибке разбора — пробрасываем
    исключение наверх (там решается fail-open/closed). SIGALRM есть только на
    Unix и только в главном потоке; на прочих платформах зовём detect напрямую."""
    import signal
    if _TIMEOUT_S <= 0 or not hasattr(signal, "setitimer"):
        return detect(cmd)

    def _on_alarm(signum, frame):
        raise _Timeout()

    old = signal.signal(signal.SIGALRM, _on_alarm)
    try:
        signal.setitimer(signal.ITIMER_REAL, _TIMEOUT_S)
        return detect(cmd)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def _cli_test(argv) -> int:
    """`--test [--mode M] "<команда>"` — dry-run без исполнения хука.
    Печатает решение, возвращает код выхода: 0=proceed, 1=ask."""
    mode = "default"
    rest = []
    i = 0
    while i < len(argv):
        if argv[i] == "--mode" and i + 1 < len(argv):
            mode = argv[i + 1]
            i += 2
            continue
        if argv[i] == "--test":
            i += 1
            continue
        rest.append(argv[i])
        i += 1
    cmd = " ".join(rest)
    try:
        result = _safe_detect(cmd)
    except Exception:
        print(f"{'ask' if _FAIL_CLOSED else 'proceed'} (разбор не удался; "
              f"fail-{'closed' if _FAIL_CLOSED else 'open'})")
        return 1 if _FAIL_CLOSED else 0
    if result:
        severity, reason = result
        if mode in RELAXED_MODES and severity != CRIT:
            print(f"proceed (bypass: ORDINARY пропущен) — {reason}")
            return 0
        print(f"ask [{severity}] — {reason}")
        return 1
    print("proceed (деструктив не обнаружен)")
    return 0


def main():
    if "--test-notify" in sys.argv[1:]:
        _alert("демо-уведомление: rm -rf /var/data")
        tn = shutil.which("terminal-notifier")
        bundle = _term_bundle_id() or "?"
        if tn and bundle != "?":
            print(f"баннер отправлен через terminal-notifier от имени {bundle} — "
                  f"клик должен вернуть в терминал")
        else:
            miss = "terminal-notifier не найден" if not tn else "терминал не определён"
            print(f"баннер отправлен через osascript-фолбэк ({miss}) — "
                  f"клик откроет Script Editor, не терминал")
        sys.exit(0)

    if "--test" in sys.argv[1:]:
        sys.exit(_cli_test(sys.argv[1:]))

    try:
        data = json.load(sys.stdin)
    except Exception:
        proceed()

    cmd = ((data.get("tool_input") or {}).get("command") or "")
    if not isinstance(cmd, str) or not cmd.strip():
        proceed()

    try:
        result = _safe_detect(cmd)
    except Exception:
        # fail-open по умолчанию: не мешаем работе. fail-closed → спросить.
        if _FAIL_CLOSED:
            ask("детектор не смог разобрать команду в срок (fail-closed)")
        proceed()
    if result:
        severity, reason = result
        mode = data.get("permission_mode") or ""
        # в bypass поднимаем y/n только на CRITICAL; ORDINARY проходит молча
        if mode in RELAXED_MODES and severity != CRIT:
            proceed()
        ask(reason)
    proceed()


if __name__ == "__main__":
    main()

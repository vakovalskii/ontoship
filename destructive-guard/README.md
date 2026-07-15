# destructive-guard

`PreToolUse` Bash-хук для Claude Code: детектит команды на удаление и вместо глухого
блока поднимает интерактивное подтверждение **y/n** — работает даже в
`bypassPermissions`. При детекте — звук + macOS-баннер.

Разбор по токенам (первый токен каждой простой команды после split по `; && || | & ( ) \``),
поэтому `perform`/`transform`/`terraform` **не** ловятся.

## Что ловит

| Класс | Команды |
|---|---|
| Файлы | `rm`, `rmdir`, `shred`, `unlink`, `srm`, `truncate`, `dropdb` |
| find | `find … -delete`, `find … -exec rm\|unlink\|shred\|srm\|rmdir` |
| git | `git rm`, `git clean -f`, `git reset --hard`, `git push --force/-f`, `git branch -D`, `git filter-branch`, `git stash clear`, `git update-ref -d` |
| docker/podman | `rm`/`rmi`, `(volume\|image\|system\|network\|container\|builder) prune`, `(volume\|image\|network\|container) rm`, `compose down -v` |
| SQL | `DROP TABLE\|DATABASE\|SCHEMA`, `TRUNCATE`, `DELETE FROM` (при наличии db-клиента: psql/mysql/mongosh/sqlite3/…) |
| NoSQL/кэш | mongo `dropDatabase()`/`.drop()`/`deleteMany()`; `redis-cli FLUSHALL\|FLUSHDB` |
| Диск/ФС | `dd of=/dev/…`, `mkfs`/`mkfs.*`, `wipefs`, `blkdiscard` |
| Kubernetes | `kubectl delete …` |
| IaC | `terraform\|tofu\|terragrunt destroy` (и `apply -destroy`), `pulumi destroy` |
| Cloud | `aws s3 rb`/`rm --recursive`, `aws … delete-*/terminate-*/remove-*`; `gcloud … delete`; `az … delete` |
| Прочее | `crontab -r` |

Закрытые обходы: `\rm`, `git -C path reset`, `docker --context prod rm`,
`kubectl -n prod delete`, `terraform -chdir=infra destroy`, `bash -c "rm …"`,
`bash <<< "rm …"` (here-string), `… \| xargs rm`.

**Вне охвата** (осознанно, чтобы не убить signal-to-noise): деструктив внутри файла/скрипта
(`python x.py`, `psql -f f.sql`, `bash x.sh`), редиректы `>`, `docker compose down` без `-v`,
`git push --force-with-lease`.

## Severity-тиринг

Каждый детект помечается `critical` или `ordinary`:

- **CRITICAL** — необратимо или бьёт за пределы рабочей копии (`rm -rf` по абс./системным
  путям, `~`, `*`; `shred`/`srm`/`dropdb`; `dd` на устройство, `mkfs`/`wipefs`;
  `git reset --hard`/`push --force`/`branch -D`/`filter-branch`; `docker … prune`/`volume rm`/
  `compose down -v`; `kubectl delete`, `terraform/pulumi destroy`; cloud-удаление
  (`aws terminate`/`gcloud delete`/`az delete`); `redis FLUSHALL`; SQL/NoSQL-удаление;
  `crontab -r`).
- **ORDINARY** — локальное обратимое удаление (`rm file`, `rm -rf ./build|node_modules|dist`,
  `rmdir`, `git rm`, `docker rm/rmi`, `dd of=file`, `aws s3 rm <один-объект>`).

В `bypassPermissions` y/n поднимается **только на CRITICAL**; ORDINARY проходит молча
(раз пользователь явно ушёл в bypass). В остальных режимах — ask на любой детект.

## Уведомления (macOS)

По детекту — системный звук + баннер. Чтобы **клик по баннеру возвращал в терминал**
(iTerm2/Terminal/VS Code/…), а не открывал Script Editor, поставь `terminal-notifier`:

```bash
brew install terminal-notifier
```

Без него баннер и звук всё равно работают (osascript-фолбэк), но клик уводит в Script
Editor — это ограничение `osascript display notification`, обойти без стороннего
инструмента нельзя. Терминал определяется автоматически (`__CFBundleIdentifier` →
`TERM_PROGRAM`); можно задать явно через `NDG_TERM_BUNDLE`.

Проверить уведомление:

```bash
python3 hooks/confirm-destructive.py --test-notify
```

## CLI dry-run

Прогнать команду через детектор без исполнения хука:

```bash
python3 hooks/confirm-destructive.py --test "rm -rf /var/data"
# ask [critical] — удаление: rm -rf /var/data          (exit 1)

python3 hooks/confirm-destructive.py --test --mode bypassPermissions "rm file.txt"
# proceed (bypass: ORDINARY пропущен) — удаление: rm file.txt   (exit 0)
```

Код выхода: `0` = proceed, `1` = ask.

## Настройки (env)

| Переменная | Дефолт | Что делает |
|---|---|---|
| `NDG_NOTIFY` | `1` | `0` — выключить звук/баннер |
| `NDG_SOUND` | `Funk` | системный звук macOS (Funk/Sosumi/Basso/Glass/Ping/Hero…) |
| `NDG_TERM_BUNDLE` | — | bundle-id терминала для клика по баннеру (переопределяет автоопределение) |
| `NDG_TIMEOUT_MS` | `200` | внутренний лимит на разбор одной команды |
| `NDG_FAIL_CLOSED` | `0` | `1` — при сбое/таймауте разбора спрашивать (по умолчанию — fail-open: пропускать) |

## Тесты

```bash
python3 tests/test_hook.py
```

Stdlib-only, уведомления в тестах заглушены (`NDG_NOTIFY=0`).

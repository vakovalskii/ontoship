#!/usr/bin/env python3
"""Тесты хука confirm-destructive: кормим JSON-пейлоады, проверяем решение
(ask на удаление / молчим на безопасном). Stdlib-only, без зависимостей.

Запуск:  python3 tests/test_hook.py   (или:  python3 -m unittest -v)
Звук/баннер заглушены через NDG_NOTIFY=0, так что тесты безопасны и кросс-платформенны.
"""
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..")
HOOK = os.path.join(ROOT, "destructive-guard", "hooks", "confirm-destructive.py")

# (описание, команда, ждём_ask)
ASK_CASES = [
    ("rm file", "rm /tmp/x"),
    ("rm -rf", "rm -rf build/"),
    ("&& rm", "cd /tmp && rm foo"),
    ("sudo rm", "sudo rm -rf /var/x"),
    ("FOO=bar rm", "FOO=1 rm x"),
    ("rmdir", "rmdir olddir"),
    ("shred", "shred -u secret.key"),
    ("unlink", "unlink /tmp/link"),
    ("truncate", "truncate -s 0 file.log"),
    ("git rm", "git rm --cached f"),
    ("git clean", "git clean -fd"),
    ("git reset --hard", "git reset --hard origin/main"),
    ("git push --force", "git push --force origin main"),
    ("git push -f", "git push -f"),
    ("find -delete", "find . -name '*.tmp' -delete"),
    ("find -exec rm", "find . -name x -exec rm {} ;"),
    ("docker rm", "docker rm -f c1"),
    ("docker rmi", "docker rmi img"),
    ("docker volume rm", "docker volume rm data"),
    ("docker volume prune", "docker volume prune -f"),
    ("docker system prune", "docker system prune -a"),
    ("compose down -v", "docker compose down -v"),
    ("compose down --volumes", "docker compose down --volumes"),
    ("docker-compose down -v", "docker-compose down -v"),
    ("psql DELETE FROM", 'psql -c "DELETE FROM users"'),
    ("psql DROP TABLE", 'psql -d hub -c "DROP TABLE x"'),
    ("sqlite TRUNCATE", 'sqlite3 db "TRUNCATE TABLE t"'),
    ("subshell rm", "echo $(rm secret)"),
    # закрытые обходы (red-team D/E)
    ("backslash rm", "\\rm /tmp/x"),
    ("git -C reset --hard", "git -C /repo reset --hard"),
    ("git -c rm", "git -c core.editor=vim rm f"),
    ("git --git-dir= reset", "git --git-dir=/r reset --hard"),
    ("git branch -D", "git branch -D feature"),
    ("bash -c rm", 'bash -c "rm -rf build"'),
    ("sh -c rm", "sh -c 'rm x'"),
    ("bash -lc rm", 'bash -lc "rm x"'),
    ("xargs rm", "find . -name '*.tmp' | xargs rm"),
    ("xargs -0 rm", "find . -print0 | xargs -0 rm -rf"),
    ("xargs -I rm", "echo x | xargs -I {} rm {}"),
    ("docker --context rm", "docker --context prod rm -f c1"),
    ("docker -H rmi", "docker -H tcp://x:2375 rmi img"),
    ("find -exec unlink", "find . -name x -exec unlink {} ;"),
    ("bash -c psql delete", "bash -c \"psql -c 'DELETE FROM t'\""),
    # here-string и heredoc-body (позаимствовано у dcg)
    ("here-string rm", 'bash <<< "rm -rf build"'),
    ("here-string rm abs", 'sh <<< "rm -rf /var/x"'),
    ("heredoc body rm", "cat <<EOF | bash\nrm -rf /\nEOF"),
    # расширенное покрытие команд
    ("dd device", "dd if=/dev/zero of=/dev/disk0 bs=1m"),
    ("dd file", "dd if=a of=backup.img"),
    ("mkfs", "mkfs.ext4 /dev/sdb1"),
    ("wipefs", "wipefs -a /dev/sda"),
    ("blkdiscard", "blkdiscard /dev/nvme0n1"),
    ("kubectl delete", "kubectl delete namespace prod"),
    ("kubectl -n delete", "kubectl -n prod delete pod x"),
    ("terraform destroy", "terraform destroy -auto-approve"),
    ("terraform -chdir destroy", "terraform -chdir=infra destroy"),
    ("terraform apply -destroy", "terraform apply -destroy"),
    ("tofu destroy", "tofu destroy"),
    ("pulumi destroy", "pulumi destroy -y"),
    ("redis flushall", "redis-cli FLUSHALL"),
    ("redis -h flushdb", "redis-cli -h db -n 0 flushdb"),
    ("aws s3 rb", "aws s3 rb s3://bucket --force"),
    ("aws ec2 terminate", "aws ec2 terminate-instances --instance-ids i-123"),
    ("aws delete-table", "aws dynamodb delete-table --table-name t"),
    ("aws s3 rm --recursive", "aws s3 rm s3://b/ --recursive"),
    ("aws s3 rm single", "aws s3 rm s3://b/key.txt"),
    ("gcloud delete", "gcloud compute instances delete vm1"),
    ("az delete", "az group delete --name rg"),
    ("git filter-branch", "git filter-branch --tree-filter x HEAD"),
    ("git stash clear", "git stash clear"),
    ("git update-ref -d", "git update-ref -d refs/heads/x"),
    ("crontab -r", "crontab -r"),
    ("mongo dropDatabase", 'mongosh --eval "db.dropDatabase()"'),
    ("mongo deleteMany", 'mongosh --eval "db.users.deleteMany({})"'),
]

PROCEED_CASES = [
    ("perform/transform/confirm", 'echo "performing transform, confirm"'),
    ("terraform/warm", "npm run build && echo terraform warm"),
    ("git push normal", "git push origin main"),
    ("git push --force-with-lease", "git push --force-with-lease"),
    ("compose down (no -v)", "docker compose down"),
    ("grep DELETE FROM (no db client)", "grep -r 'DELETE FROM' src/"),
    ("redirect >", "echo hi > out.txt"),
    ("docker ps", "docker ps -a"),
    ("git status/add", "git status && git add -A"),
    ("mv not rm", "mv a b"),
    ("ls/cat", "ls -la && cat file.txt"),
    ("npm install", "npm install && npm run build"),
    # не путать с закрытыми обходами: интерпретатор/флаги без удаления
    ("bash -c safe", 'bash -c "ls -la"'),
    ("here-string safe", 'bash <<< "ls -la"'),
    ("xargs cat", "ls | xargs cat"),
    ("git -C status", "git -C /repo status"),
    ("git branch list", "git branch -a"),
    ("docker --context ps", "docker --context prod ps -a"),
    # расширенное покрытие: не-деструктивные соседи новых команд
    ("dd no of=", "dd if=disk.img | gzip > out.gz"),
    ("kubectl get", "kubectl get pods -n prod"),
    ("terraform plan", "terraform plan -out plan.tfplan"),
    ("terraform apply", "terraform apply -auto-approve"),
    ("pulumi up", "pulumi up -y"),
    ("aws s3 ls", "aws s3 ls s3://b"),
    ("aws describe", "aws ec2 describe-instances"),
    ("gcloud list", "gcloud compute instances list"),
    ("az list", "az group list"),
    ("redis get", "redis-cli GET session:1"),
    ("git stash pop", "git stash pop"),
    ("git stash list", "git stash list"),
    ("crontab -l", "crontab -l"),
    ("crontab -e", "crontab -e"),
]


def decision(command: str, mode: str = "default") -> str:
    """Вернуть 'ask' | 'proceed' от хука для данной команды в заданном режиме."""
    payload = json.dumps({"tool_name": "Bash", "permission_mode": mode,
                          "tool_input": {"command": command}})
    env = dict(os.environ, NDG_NOTIFY="0")
    r = subprocess.run([sys.executable, HOOK], input=payload,
                       capture_output=True, text=True, env=env)
    out = (r.stdout or "").strip()
    if not out:
        return "proceed"
    try:
        return json.loads(out)["hookSpecificOutput"]["permissionDecision"]
    except Exception:
        return "proceed"


# В bypassPermissions поднимаем y/n ТОЛЬКО на CRITICAL (необратимо / бьёт за
# пределы рабочей копии).
BYPASS_CRITICAL = [
    ("rm -rf абс. путь", "rm -rf /var/data"),
    ("rm -rf ~", "rm -rf ~/Documents"),
    ("rm -r не-регенер.", "rm -r src"),
    ("rm -rf glob", "rm -rf *"),
    ("rm -rf .", "rm -rf ."),
    ("shred", "shred -u key"),
    ("srm", "srm secret"),
    ("dropdb", "dropdb mydb"),
    ("git reset --hard", "git reset --hard origin/main"),
    ("git clean -fd", "git clean -fd"),
    ("git push --force", "git push --force origin main"),
    ("git branch -D", "git branch -D feature"),
    ("docker volume rm", "docker volume rm data"),
    ("docker system prune", "docker system prune -a"),
    ("compose down -v", "docker compose down -v"),
    ("psql DELETE FROM", 'psql -c "DELETE FROM users"'),
    ("sqlite DROP TABLE", 'sqlite3 db "DROP TABLE t"'),
    ("find -delete", "find . -name '*.tmp' -delete"),
    ("find -exec rm", "find . -exec rm {} ;"),
    ("git -C reset --hard", "git -C /repo reset --hard"),
    ("bash -c rm -rf abs", 'bash -c "rm -rf /etc/x"'),
    # расширенное покрытие (всё CRITICAL → спрашивает и в bypass)
    ("dd device", "dd if=/dev/zero of=/dev/disk0"),
    ("mkfs", "mkfs.ext4 /dev/sdb1"),
    ("kubectl delete", "kubectl delete namespace prod"),
    ("terraform destroy", "terraform destroy -auto-approve"),
    ("pulumi destroy", "pulumi destroy -y"),
    ("redis flushall", "redis-cli FLUSHALL"),
    ("aws terminate", "aws ec2 terminate-instances --instance-ids i-1"),
    ("gcloud delete", "gcloud compute instances delete vm1"),
    ("git filter-branch", "git filter-branch --tree-filter x HEAD"),
    ("crontab -r", "crontab -r"),
    ("mongo dropDatabase", 'mongosh --eval "db.dropDatabase()"'),
]

# В bypassPermissions проходят молча, но в default — спрашивают (ORDINARY).
BYPASS_ORDINARY = [
    ("rm file", "rm file.txt"),
    ("rm -f files", "rm -f a.log b.log"),
    ("rm -rf ./build", "rm -rf ./build"),
    ("rm -rf node_modules", "rm -rf node_modules"),
    ("rm -rf dist/", "rm -rf dist/"),
    ("rmdir", "rmdir olddir"),
    ("unlink", "unlink /tmp/link"),
    ("truncate", "truncate -s 0 x.log"),
    ("git rm", "git rm --cached f"),
    ("docker rm", "docker rm -f c1"),
    ("docker rmi", "docker rmi img"),
    ("docker image rm", "docker image rm img"),
    # расширенное покрытие: ORDINARY (bypass пропускает, default спрашивает)
    ("dd file", "dd if=a of=backup.img"),
    ("aws s3 rm single", "aws s3 rm s3://b/key.txt"),
]


class TestConfirmDestructive(unittest.TestCase):
    def test_hook_exists(self):
        self.assertTrue(os.path.exists(HOOK), f"hook not found: {HOOK}")

    def test_destructive_asks(self):
        for desc, cmd in ASK_CASES:
            with self.subTest(case=desc, cmd=cmd):
                self.assertEqual(decision(cmd), "ask", f"ожидали ask: {cmd}")

    def test_safe_proceeds(self):
        for desc, cmd in PROCEED_CASES:
            with self.subTest(case=desc, cmd=cmd):
                self.assertEqual(decision(cmd), "proceed", f"не должно спрашивать: {cmd}")

    def test_empty_command_proceeds(self):
        self.assertEqual(decision(""), "proceed")

    def test_bypass_critical_still_asks(self):
        for desc, cmd in BYPASS_CRITICAL:
            with self.subTest(case=desc, cmd=cmd):
                self.assertEqual(decision(cmd, "bypassPermissions"), "ask",
                                 f"CRITICAL должен спрашивать даже в bypass: {cmd}")

    def test_bypass_ordinary_proceeds_but_asks_default(self):
        for desc, cmd in BYPASS_ORDINARY:
            with self.subTest(case=desc, cmd=cmd):
                self.assertEqual(decision(cmd, "bypassPermissions"), "proceed",
                                 f"ORDINARY должен молча проходить в bypass: {cmd}")
                self.assertEqual(decision(cmd, "default"), "ask",
                                 f"ORDINARY должен спрашивать в default: {cmd}")

    def test_cli_test_mode(self):
        env = dict(os.environ, NDG_NOTIFY="0")

        def run(*extra):
            return subprocess.run([sys.executable, HOOK, "--test", *extra],
                                  capture_output=True, text=True, env=env)

        r = run("rm -rf /var/data")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("ask", r.stdout)

        r = run("ls -la")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("proceed", r.stdout)

        # ORDINARY + bypass → proceed (код 0)
        r = run("--mode", "bypassPermissions", "rm file.txt")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_manifests_valid_json(self):
        for rel in (".claude-plugin/marketplace.json",
                    "destructive-guard/.claude-plugin/plugin.json",
                    "destructive-guard/hooks/hooks.json"):
            with self.subTest(file=rel):
                with open(os.path.join(ROOT, rel)) as f:
                    json.load(f)

    def test_marketplace_source_points_to_plugin(self):
        with open(os.path.join(ROOT, ".claude-plugin", "marketplace.json")) as f:
            mp = json.load(f)
        for plugin in mp["plugins"]:
            src = plugin["source"]
            with self.subTest(plugin=plugin["name"], source=src):
                # локальный источник: "." (корень) или относительный "./путь"
                self.assertTrue(src == "." or src.startswith("./"),
                                f"source must be '.' or start with './' : {src}")
                self.assertTrue(
                    os.path.exists(os.path.join(ROOT, src, ".claude-plugin", "plugin.json")),
                    f"plugin.json not found at source {src}",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)

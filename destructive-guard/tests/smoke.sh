#!/usr/bin/env bash
# Smoke-тест destructive-guard: юнит-тесты + dry-run по всем категориям +
# живое уведомление. Одна команда, читаемый вывод.
#
#   bash destructive-guard/tests/smoke.sh          # всё
#   bash destructive-guard/tests/smoke.sh --no-notify   # без баннера
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
H="$HERE/../hooks/confirm-destructive.py"
NOTIFY=1
[ "${1:-}" = "--no-notify" ] && NOTIFY=0

pass=0; fail=0
# check <ожидаемое: ask|proceed> <команда> [режим]
check() {
  local want="$1" cmd="$2" mode="${3:-default}"
  local out; out="$(python3 "$H" --test --mode "$mode" "$cmd")"
  local got=proceed; case "$out" in ask*) got=ask;; esac
  if [ "$got" = "$want" ]; then
    pass=$((pass+1)); printf '  \033[32m✓\033[0m %-8s %s\n' "[$got]" "$cmd"
  else
    fail=$((fail+1)); printf '  \033[31m✗ ждали %s, получили %s\033[0m  %s\n' "$want" "$got" "$cmd"
  fi
}

echo "════ 1. UNIT ════"
python3 "$HERE/test_hook.py" 2>&1 | tail -3

echo; echo "════ 2. ДОЛЖНЫ ЛОВИТЬСЯ (ask) ════"
check ask "rm -rf /var/data"
check ask "dd if=/dev/zero of=/dev/disk0"
check ask "mkfs.ext4 /dev/sdb1"
check ask "wipefs -a /dev/sda"
check ask "kubectl -n prod delete pod api"
check ask "terraform -chdir=infra destroy"
check ask "terraform apply -destroy"
check ask "pulumi destroy -y"
check ask "redis-cli -h db FLUSHALL"
check ask "aws s3 rb s3://bucket --force"
check ask "aws ec2 terminate-instances --instance-ids i-1"
check ask "aws dynamodb delete-table --table-name t"
check ask "gcloud compute instances delete vm1"
check ask "az group delete --name rg"
check ask "git filter-branch --tree-filter x HEAD"
check ask "git stash clear"
check ask "git reset --hard origin/main"
check ask "crontab -r"
check ask 'mongosh --eval "db.dropDatabase()"'
check ask 'bash <<< "rm -rf /etc/x"'

echo; echo "════ 3. НЕ ДОЛЖНЫ (proceed) — анти-false-positive ════"
check proceed "kubectl get pods -n prod"
check proceed "terraform plan -out plan.tfplan"
check proceed "terraform apply -auto-approve"
check proceed "pulumi up -y"
check proceed "aws s3 ls s3://b"
check proceed "aws ec2 describe-instances"
check proceed "gcloud compute instances list"
check proceed "redis-cli GET session:1"
check proceed "git stash pop"
check proceed "crontab -l"
check proceed "dd if=disk.img | gzip > out.gz"
check proceed "npm run build && echo terraform warm"
check proceed "git push --force-with-lease"

echo; echo "════ 4. BYPASS-ТИРИНГ ════"
check proceed "dd if=a of=backup.img"      bypassPermissions   # ORDINARY молчит
check ask     "dd if=a of=backup.img"      default             # ...но спрашивает в default
check ask     "kubectl delete ns prod"     bypassPermissions   # CRITICAL всегда

if [ "$NOTIFY" = 1 ]; then
  echo; echo "════ 5. ЖИВОЕ УВЕДОМЛЕНИЕ (смотри на экран) ════"
  python3 "$H" --test-notify
fi

echo; echo "──────────────────────────────"
printf 'dry-run: \033[32m%d ok\033[0m / \033[31m%d fail\033[0m\n' "$pass" "$fail"
[ "$fail" = 0 ]

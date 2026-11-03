#!/bin/sh
set -eu

backup_directory=${BACKUP_DIRECTORY:-/mnt/tamirabot-backups}
alert_threshold=${BACKUP_DISK_ALERT_PCT:-85}
alert_email=${BACKUP_DISK_ALERT_EMAIL:-}

case "$alert_threshold" in
  ''|*[!0-9]*)
    echo "BACKUP_DISK_ALERT_PCT must be an integer from 1 to 99." >&2
    exit 2
    ;;
esac
if [ "$alert_threshold" -lt 1 ] || [ "$alert_threshold" -gt 99 ]; then
  echo "BACKUP_DISK_ALERT_PCT must be an integer from 1 to 99." >&2
  exit 2
fi
if [ ! -d "$backup_directory" ]; then
  echo "Backup directory does not exist: $backup_directory" >&2
  exit 2
fi

used_percent=$(df -P "$backup_directory" | awk 'NR == 2 { gsub(/%/, "", $5); print $5 }')
case "$used_percent" in
  ''|*[!0-9]*)
    echo "Could not read disk usage for $backup_directory." >&2
    exit 2
    ;;
esac

if [ "$used_percent" -ge "$alert_threshold" ]; then
  message="Tamirabot backup disk alert: $backup_directory is ${used_percent}% full (alert threshold ${alert_threshold}%)."
  logger -p user.warning -t tamirabot-backup-disk "$message"
  echo "$message" >&2
  if [ -n "$alert_email" ]; then
    if command -v mail >/dev/null 2>&1; then
      printf '%s\n' "$message" | mail -s "Tamirabot backup disk space alert" "$alert_email"
    else
      logger -p user.err -t tamirabot-backup-disk "BACKUP_DISK_ALERT_EMAIL is set but the mail command is unavailable."
    fi
  fi
  exit 1
fi

logger -p user.info -t tamirabot-backup-disk "$backup_directory is ${used_percent}% full."

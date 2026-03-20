import shutil
import json
from datetime import datetime
from pathlib import Path

from nanobot.config.paths import get_backup_dir


backup_root = get_backup_dir()


def backup_files(files: list[Path]) -> None:
    """Backup files to the backup directory."""
    backup_date = datetime.now().strftime("%Y-%m-%d")
    backup_time = datetime.now().strftime("%H%M%S")
    backup_dir = backup_root / backup_date / backup_time
    backup_dir.mkdir(parents=True, exist_ok=True)

    backup_list = []

    for i, file in enumerate(files):
        shutil.copy(file, backup_dir / f"{i}_{file.name}")
        backup_list.append({
            "raw_path": file.as_posix(),
            "current_path": f"{i}_{file.name}".as_posix(),
        })

    backup_config = {
        "backup_time": f"{backup_date} {backup_time}",
        "files": backup_list,
    }

    with open(backup_dir / "backup.json", "w") as f:
        json.dump(backup_config, f, indent=2)

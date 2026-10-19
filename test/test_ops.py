import json
import tarfile
from io import BytesIO

import pytest

from coding_agent.accounts import Accounts
from coding_agent.ops import backup, restore


def test_backup_restore_roundtrip_accounts_usage_and_documents(tmp_path):
    root = tmp_path / "data"
    accounts = Accounts(root)
    user_id = accounts.create_user("alice", "test-password-for-backup")
    accounts.reserve(user_id, 123, 10, 1000)
    token = accounts.login("alice", "test-password-for-backup", "test")
    document = root / "users" / user_id / "notes.md"
    document.parent.mkdir(parents=True)
    document.write_text("# 中文资料", encoding="utf-8")
    (root / "maintenance.flag").touch()
    archive = tmp_path / "backup.tar.gz"
    backup(root, archive)
    restored = tmp_path / "restored"
    restore(archive, restored)
    assert (restored / "users" / user_id / "notes.md").read_text(encoding="utf-8") == "# 中文资料"
    assert Accounts(restored).usage(user_id)["charged_tokens"] == 123
    assert Accounts(restored).authenticate(token) is None
    assert not (restored / "maintenance.flag").exists()
    with pytest.raises(ValueError, match="empty"):
        restore(archive, restored)
    with pytest.raises(FileExistsError):
        backup(root, archive)


@pytest.mark.parametrize("member", ["../escaped.txt", "data/../../escaped.txt", "/absolute.txt", "data/link.txt"])
def test_restore_rejects_traversal_and_links(tmp_path, member):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        data = json.dumps({"format": 1, "database_version": 1, "files": {}}).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(data)
        tar.addfile(info, BytesIO(data))
        item = tarfile.TarInfo(member)
        if member.endswith("link.txt"):
            item.type = tarfile.SYMTYPE
            item.linkname = "/etc/passwd"
        tar.addfile(item)
    with pytest.raises(ValueError, match="unsafe"):
        restore(archive, tmp_path / "restore")
    assert not (tmp_path / "escaped.txt").exists()


def test_restore_checks_file_content_hash(tmp_path):
    archive = tmp_path / "corrupt.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        data = json.dumps({"format": 1, "database_version": 1, "files": {"accounts.sqlite3": "wrong-hash"}}).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(data)
        tar.addfile(info, BytesIO(data))
        file = tarfile.TarInfo("data/accounts.sqlite3")
        file.size = 3
        tar.addfile(file, BytesIO(b"bad"))
    with pytest.raises(ValueError, match="checksum"):
        restore(archive, tmp_path / "restore")
